"""SQLite-backed state: which products/videos we've already posted, and how
many we've posted today, so a crashed or re-run pipeline never double-posts
the same product or blows past the daily target.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
from pathlib import Path

_SCHEMA = """
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
