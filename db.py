"""
Sổ theo dõi trạng thái cào (checkpoint) dùng SQLite.

Mục tiêu:
  - Lưu toàn bộ gói thầu phát hiện được (pha discovery).
  - Theo dõi trạng thái tải của từng gói để RESUME chính xác, KHÔNG cào trùng.
  - Bền vững khi cào hàng trăm nghìn record, có thể dừng/chạy lại bất cứ lúc nào.

Khử trùng dựa trên `notify_id` (ID gói thầu trích từ URL chi tiết) - là khoá duy nhất.

Trạng thái (status):
  pending      : mới phát hiện, chưa tải
  in_progress  : đang được 1 worker xử lý (claim) - tránh worker khác lấy trùng
  done         : đã tải xong (đủ hoặc một phần theo tài liệu có sẵn)
  failed       : tải lỗi, sẽ được retry ở lần chạy sau (đến max_attempts)
  skipped      : đã thử quá số lần cho phép -> bỏ qua

Chạy song song nhiều worker: dùng claim_next() (transaction IMMEDIATE) để mỗi gói
chỉ được đúng một worker nhận. Nếu worker crash, gói 'in_progress' quá hạn sẽ được
recover về 'pending' ở lần khởi động sau (xem reclaim_stale).
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
    worker        TEXT,                         -- worker đang/đã xử lý
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
        # isolation_level=None -> autocommit; ta tự quản lý transaction (BEGIN IMMEDIATE)
        # để claim_next atomic an toàn khi nhiều worker cùng ghi.
        self._conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # WAL để đọc/ghi song song tốt hơn khi chạy dài
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        # Chờ tối đa 30s khi DB bị khoá bởi worker khác (tránh 'database is locked')
        self._conn.execute("PRAGMA busy_timeout=30000;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # Autocommit mode -> mở transaction tường minh để gom câu lệnh atomic
        self._conn.execute("BEGIN")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
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

    def claim_next(self, max_attempts: int, worker_id: str) -> Optional[sqlite3.Row]:
        """Giành (claim) 1 gói để xử lý theo cách ATOMIC cho nhiều worker.

        Dùng transaction IMMEDIATE để khoá ghi -> chỉ 1 worker nhận được 1 gói.
        Đánh dấu gói thành 'in_progress', tăng attempts, gán worker_id.
        Trả về row đã claim, hoặc None nếu không còn gói.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                """SELECT * FROM bids
                   WHERE status IN ('pending','failed') AND attempts < ?
                   ORDER BY discovered_at ASC LIMIT 1""",
                (max_attempts,),
            ).fetchone()
            if row is None:
                self._conn.execute("COMMIT")
                return None
            self._conn.execute(
                """UPDATE bids
                   SET status='in_progress', attempts=attempts+1,
                       worker=?, updated_at=?
                   WHERE notify_id=?""",
                (worker_id, time.time(), row["notify_id"]),
            )
            self._conn.execute("COMMIT")
            return row
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def reclaim_stale(self, stale_seconds: int = 900) -> int:
        """Đưa các gói 'in_progress' quá hạn (worker crash) về 'pending' để retry.

        Trả số gói được khôi phục.
        """
        cutoff = time.time() - stale_seconds
        with self._tx() as c:
            cur = c.execute(
                """UPDATE bids SET status='pending'
                   WHERE status='in_progress' AND updated_at < ?""",
                (cutoff,),
            )
            return cur.rowcount

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
