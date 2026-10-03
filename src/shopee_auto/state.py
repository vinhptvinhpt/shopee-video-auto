"""SQLite-backed state. `product_queue` is the single source of truth for a
product's whole lifecycle and is deduped by affiliate link (UNIQUE), so a
product can only ever enter the queue once no matter how many overlapping
CSV exports you import:

    pending -> video_ready -> posted
                  \\-> prepare_failed   \\-> post_failed
    (any non-'posted' status) -> skipped (manual)

- "Tìm & tải video" (Pipeline.prepare_videos) moves pending -> video_ready
  (or prepare_failed), downloading a matching TikTok clip but never
  touching the phone.
- "Đăng bài" (Pipeline.post_ready) moves video_ready -> posted (or
  post_failed), driving the phone with the already-downloaded clip.

Products left in the queue past a day's target simply stay in whatever
status they're in and are picked up on a later run -- that's the entire
overflow mechanism, no separate "carry over" logic needed.

`imported_files` tracks which CSVs (by content hash, not filename/mtime)
have already been scanned, so re-running the folder scan never re-reads a
file it already ingested.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
from pathlib import Path

from shopee_auto.product_source import Product

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_link TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    product_url TEXT,
    sales_count INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    video_path TEXT,
    tiktok_source_url TEXT,
    queued_at TEXT NOT NULL,
    prepared_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS imported_files (
    fingerprint TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    products_added INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_link TEXT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""

# Columns added after the first release -- ALTER TABLE so an existing
# data/state.db (with products already queued) upgrades in place instead of
# needing a re-import.
_MIGRATIONS: list[tuple[str, str]] = [
    ("video_path", "ALTER TABLE product_queue ADD COLUMN video_path TEXT"),
    ("tiktok_source_url", "ALTER TABLE product_queue ADD COLUMN tiktok_source_url TEXT"),
    ("prepared_at", "ALTER TABLE product_queue ADD COLUMN prepared_at TEXT"),
    ("finished_at", "ALTER TABLE product_queue ADD COLUMN finished_at TEXT"),
]


@dataclasses.dataclass
class QueueItem:
    product: Product
    status: str
    video_path: str | None = None
    tiktok_source_url: str | None = None
    queued_at: str | None = None


def _row_to_item(row: sqlite3.Row) -> QueueItem:
    product = Product(
        name=row["product_name"],
        link=row["product_link"],
        product_url=row["product_url"] or "",
        sales_count=row["sales_count"],
    )
    return QueueItem(
        product=product,
        status=row["status"],
        video_path=row["video_path"],
        tiktok_source_url=row["tiktok_source_url"],
        queued_at=row["queued_at"],
    )


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(product_queue)")}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                self._conn.execute(ddl)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- CSV import tracking -------------------------------------------

    def is_file_imported(self, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM imported_files WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return row is not None

    def mark_file_imported(self, fingerprint: str, file_name: str, products_added: int) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO imported_files (fingerprint, file_name, products_added, imported_at) "
            "VALUES (?, ?, ?, ?)",
            (fingerprint, file_name, products_added, now),
        )
        self._conn.commit()

    # -- queue: import -----------------------------------------------

    def enqueue_products(self, products: list[Product]) -> tuple[int, int]:
        """Add newly-imported products to the durable queue. A product
        already present (any status -- from this import or any earlier
        one) is silently skipped, so importing overlapping CSV exports
        never creates duplicates or re-attempts. Returns (added,
        duplicates)."""
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        added = 0
        for p in products:
            cur = self._conn.execute(
                """
                INSERT INTO product_queue (product_link, product_name, product_url, sales_count, status, queued_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(product_link) DO NOTHING
                """,
                (p.link, p.name, p.product_url, p.sales_count, now),
            )
            added += cur.rowcount
        self._conn.commit()
        return added, len(products) - added

    # -- queue: prepare phase (pending -> video_ready / prepare_failed) -

    def get_pending_for_prepare(self, limit: int | None = None) -> list[Product]:
        """limit=None pulls every pending product (used by the "Tìm & tải
        video" dashboard action, which isn't gated by daily_target)."""
        rows = self._conn.execute(
            "SELECT product_link, product_name, product_url, sales_count "
            "FROM product_queue WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (-1 if limit is None else limit,),
        ).fetchall()
        return [
            Product(name=r["product_name"], link=r["product_link"], product_url=r["product_url"] or "", sales_count=r["sales_count"])
            for r in rows
        ]

    def mark_video_ready(self, product_link: str, video_path: str, tiktok_source_url: str) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE product_queue SET status = 'video_ready', video_path = ?, "
            "tiktok_source_url = ?, prepared_at = ? WHERE product_link = ?",
            (video_path, tiktok_source_url, now, product_link),
        )
        self._conn.commit()

    def mark_prepare_failed(self, product_link: str) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE product_queue SET status = 'prepare_failed', prepared_at = ? WHERE product_link = ?",
            (now, product_link),
        )
        self._conn.commit()

    # -- queue: post phase (video_ready -> posted / post_failed) --------

    def get_ready_to_post(self, limit: int) -> list[QueueItem]:
        rows = self._conn.execute(
            "SELECT * FROM product_queue WHERE status = 'video_ready' ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_item(r) for r in rows]

    def mark_posted(self, product_link: str, status: str) -> None:
        """status is 'posted' or 'post_failed'."""
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE product_queue SET status = ?, finished_at = ? WHERE product_link = ?",
            (status, now, product_link),
        )
        self._conn.commit()

    def count_posted_today(self) -> int:
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        row = self._conn.execute(
            "SELECT COUNT(*) FROM product_queue WHERE status = 'posted' AND finished_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row[0] if row else 0

    # -- queue: management / dashboard ----------------------------------

    def count_pending_queue(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM product_queue WHERE status = 'pending'"
        ).fetchone()
        return row[0] if row else 0

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM product_queue GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def list_queue(self, status: str | None = None, limit: int = 100) -> list[QueueItem]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM product_queue WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM product_queue ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def skip_queue_item(self, product_link: str) -> bool:
        """Manually pull an item out of the pool run_daily/prepare/post
        pick from. Returns False if it's already posted (can't un-post)."""
        cur = self._conn.execute(
            "UPDATE product_queue SET status = 'skipped' WHERE product_link = ? AND status != 'posted'",
            (product_link,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def requeue_item(self, product_link: str) -> bool:
        """Put a failed/skipped item back to 'pending' for another attempt."""
        cur = self._conn.execute(
            "UPDATE product_queue SET status = 'pending', video_path = NULL, "
            "tiktok_source_url = NULL, prepared_at = NULL, finished_at = NULL "
            "WHERE product_link = ? AND status != 'posted'",
            (product_link,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def log_stage(
        self, product_link: str | None, stage: str, status: str, detail: str = ""
    ) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO run_log (product_link, stage, status, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (product_link, stage, status, detail, now),
        )
        self._conn.commit()

    def recent_log(self, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
