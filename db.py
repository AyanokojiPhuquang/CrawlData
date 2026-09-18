"""
Sổ theo dõi trạng thái cào (checkpoint) dùng SQLite.

Mục tiêu:
  - Lưu toàn bộ gói thầu phát hiện được (pha discovery).
  - Theo dõi trạng thái tải của từng gói để RESUME chính xác, KHÔNG cào trùng.
  - Bền vững khi cào hàng trăm nghìn record, có thể dừng/chạy lại bất cứ lúc nào.

Khử trùng dựa trên `notify_id` (ID gói thầu trích từ URL chi tiết) - là khoá duy nhất.

Trạng thái (status):
  pending  : mới phát hiện, chưa tải
  done     : đã tải xong (đủ hoặc một phần theo tài liệu có sẵn)
  failed   : tải lỗi, sẽ được retry ở lần chạy sau (đến max_attempts)
  skipped  : đã thử quá số lần cho phép -> bỏ qua
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path("scrape_state.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS bids (
    notify_id     TEXT PRIMARY KEY,      -- ID duy nhất của gói thầu (từ URL)
    notify_no     TEXT,                  -- Mã TBMT (vd IB2600...)
    title         TEXT,                  -- Tên gói thầu
    href          TEXT,                  -- URL trang chi tiết
    folder        TEXT,                  -- Thư mục lưu file
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    tbmt_ok       INTEGER NOT NULL DEFAULT 0,   -- đã tải TBMT chưa
    hsmt_ok       INTEGER NOT NULL DEFAULT 0,   -- đã tải HSMT chưa
    last_error    TEXT,
    discovered_at REAL,
    updated_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_status ON bids(status);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class StateDB:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        # WAL để đọc/ghi song song tốt hơn khi chạy dài
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---------- Discovery (pha 1) ----------

    def upsert_bid(
        self, notify_id: str, notify_no: str, title: str, href: str
    ) -> bool:
        """Thêm gói thầu mới nếu chưa có. Trả True nếu là bản ghi mới."""
        now = time.time()
        with self._tx() as c:
            cur = c.execute("SELECT 1 FROM bids WHERE notify_id = ?", (notify_id,))
            if cur.fetchone():
                return False
            c.execute(
                """INSERT INTO bids
                   (notify_id, notify_no, title, href, status, discovered_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (notify_id, notify_no, title, href, now, now),
            )
            return True

    def count_by_status(self) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM bids GROUP BY status"
        )
        return {row["status"]: row["n"] for row in cur.fetchall()}

    def total(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM bids").fetchone()[0]

    # ---------- Download (pha 2) ----------

    def next_pending(self, max_attempts: int, batch: int = 1) -> list[sqlite3.Row]:
        """Lấy các gói cần tải: status pending hoặc failed (chưa quá max_attempts)."""
        cur = self._conn.execute(
            """SELECT * FROM bids
               WHERE status IN ('pending','failed') AND attempts < ?
               ORDER BY discovered_at ASC
               LIMIT ?""",
            (max_attempts, batch),
        )
        return cur.fetchall()

    def mark_in_progress(self, notify_id: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE bids SET attempts = attempts + 1, updated_at = ? WHERE notify_id = ?",
                (time.time(), notify_id),
            )

    def mark_done(
        self, notify_id: str, folder: str, tbmt_ok: bool, hsmt_ok: bool
    ) -> None:
        with self._tx() as c:
            c.execute(
                """UPDATE bids
                   SET status='done', folder=?, tbmt_ok=?, hsmt_ok=?, last_error=NULL,
                       updated_at=?
                   WHERE notify_id=?""",
                (folder, int(tbmt_ok), int(hsmt_ok), time.time(), notify_id),
            )

    def mark_failed(self, notify_id: str, error: str, max_attempts: int) -> None:
        with self._tx() as c:
            row = c.execute(
                "SELECT attempts FROM bids WHERE notify_id=?", (notify_id,)
            ).fetchone()
            attempts = row["attempts"] if row else max_attempts
            new_status = "skipped" if attempts >= max_attempts else "failed"
            c.execute(
                """UPDATE bids SET status=?, last_error=?, updated_at=? WHERE notify_id=?""",
                (new_status, error[:500], time.time(), notify_id),
            )

    # ---------- Meta (lưu tiến độ discovery) ----------

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
