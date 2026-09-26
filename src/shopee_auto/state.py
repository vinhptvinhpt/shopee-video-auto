"""SQLite-backed state:
- `imported_files` tracks which CSVs (by content hash, not filename/mtime)
  have already been scanned, so re-running the folder scan never re-reads
  a file it already ingested.
- `product_queue` is a durable, ordered queue of products (deduped by
  affiliate link, independent of whether the CSV file itself still exists
  later). Products left in the queue after a day's target is reached simply
  stay pending and are picked up on a later day -- that's the whole
  overflow mechanism, no separate "carry over" logic needed.
- `posted_products` records what's already been posted so a crashed or
  re-run pipeline never double-posts and never exceeds the daily target.
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
    queued_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imported_files (
    fingerprint TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    products_added INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posted_products (
    product_link TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    tiktok_source_url TEXT,
    status TEXT NOT NULL,
    posted_at TEXT NOT NULL
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


@dataclasses.dataclass
class PostedProduct:
    product_link: str
    product_name: str
    tiktok_source_url: str | None
    status: str
    posted_at: str


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def enqueue_products(self, products: list[Product]) -> tuple[int, int]:
        """Add newly-imported products to the durable queue. A product
        already present (pending, posted, or failed -- from this import or
        any earlier one) is silently skipped, so importing overlapping CSV
        exports never creates duplicates or re-attempts. Returns (added,
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

    def count_pending_queue(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM product_queue WHERE status = 'pending'"
        ).fetchone()
        return row[0] if row else 0

    def get_pending_queue(self, limit: int) -> list[Product]:
        rows = self._conn.execute(
            "SELECT product_link, product_name, product_url, sales_count "
            "FROM product_queue WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Product(name=name, link=link, product_url=product_url or "", sales_count=sales_count)
            for link, name, product_url, sales_count in rows
        ]

    def mark_queue_status(self, product_link: str, status: str) -> None:
        """Record the outcome of the one attempt a queued product gets
        (success/failed/skipped) -- it leaves the pending pool either way,
        so a permanently-broken product can't block the queue forever."""
        self._conn.execute(
            "UPDATE product_queue SET status = ? WHERE product_link = ?", (status, product_link)
        )
        self._conn.commit()

    def is_product_posted(self, product_link: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM posted_products WHERE product_link = ? AND status = 'success'",
            (product_link,),
        ).fetchone()
        return row is not None

    def mark_product_posted(
        self,
        product_link: str,
        product_name: str,
        tiktok_source_url: str | None,
        status: str,
    ) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO posted_products (product_link, product_name, tiktok_source_url, status, posted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_link) DO UPDATE SET
                product_name=excluded.product_name,
                tiktok_source_url=excluded.tiktok_source_url,
                status=excluded.status,
                posted_at=excluded.posted_at
            """,
            (product_link, product_name, tiktok_source_url, status, now),
        )
        self._conn.commit()

    def count_posted_today(self) -> int:
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        row = self._conn.execute(
            "SELECT COUNT(*) FROM posted_products WHERE status = 'success' AND posted_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row[0] if row else 0

    def log_stage(
        self, product_link: str | None, stage: str, status: str, detail: str = ""
    ) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO run_log (product_link, stage, status, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (product_link, stage, status, detail, now),
        )
        self._conn.commit()
