"""Cấu hình tập trung cho hệ thống cào."""
from __future__ import annotations

import logging
from pathlib import Path

# --- Đường dẫn ---
BASE_URL = "https://muasamcong.mpi.gov.vn"
LIST_URL = f"{BASE_URL}/web/guest/contractor-selection?render=index"
# Trang tìm kiếm nâng cao (để lọc theo khoảng ngày đăng tải)
SEARCH_URL = (
    f"{BASE_URL}/web/guest/contractor-selection"
    "?p_p_id=egpportalcontractorselectionv2_WAR_egpportalcontractorselectionv2"
    "&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
    "&_egpportalcontractorselectionv2_WAR_egpportalcontractorselectionv2_render=search"
)

# --- Bộ lọc theo khoảng ngày đăng tải (dd/mm/yyyy). None = không lọc ---
PUBLISH_DATE_FROM = "01/01/2026"
PUBLISH_DATE_TO = "31/01/2026"
# Tab cần cào sau khi lọc: 'all' | 'open' (chưa đóng) | 'closed' (đã đóng)
TARGET_TAB = "all"

import os as _os


def _first_existing(paths, env_key):
    """Trả đường dẫn đầu tiên tồn tại (ưu tiên biến môi trường)."""
    env_val = _os.environ.get(env_key)
    if env_val:
        return env_val
    for p in paths:
        if _os.path.exists(p):
            return p
    return paths[0]  # fallback (Selenium sẽ tìm trong PATH nếu không tồn tại)


# Đường dẫn Firefox/geckodriver — auto-detect, ghi đè được qua biến môi trường.
GECKODRIVER = _first_existing(
    ["/usr/local/bin/geckodriver", "/snap/bin/geckodriver", "/usr/bin/geckodriver"],
    "GECKODRIVER_PATH",
)
FIREFOX_BIN = _first_existing(
    ["/usr/bin/firefox", "/snap/firefox/current/usr/lib/firefox/firefox"],
    "FIREFOX_BIN",
)

OUTPUT_ROOT = Path("data")
# Thư mục tải file. Mỗi worker dùng thư mục RIÊNG để không nhặt nhầm file của nhau.
# Ghi đè qua biến môi trường WORKER_DOWNLOAD_DIR (do run_workers.sh đặt).
DOWNLOAD_DIR = Path(
    _os.environ.get("WORKER_DOWNLOAD_DIR", str(Path.home() / "Downloads"))
)
LOG_FILE = Path("scraper.log")
DB_PATH = Path("scrape_state.db")

# --- Tham số chịu tải / độ bền ---
PAGE_SIZE = 50                 # số bản ghi/trang (trang hỗ trợ 10/20/50)
MAX_ATTEMPTS = 3               # số lần thử lại tối đa cho mỗi gói
NAV_RETRIES = 5                # số lần retry điều hướng 1 URL
DOWNLOAD_TIMEOUT = 90          # giây, chờ HSMT (file lớn) tải xong
TBMT_TIMEOUT = 40              # giây, TBMT là file nhỏ -> phát hiện lỗi sớm
VIEWER_RENDER_TIMEOUT = 60     # giây, chờ viewer webform render
MAX_CONSECUTIVE_FAILS = 4      # số lỗi liên tiếp -> khởi động lại trình duyệt
RESTART_BROWSER_EVERY = 25     # khởi động lại trình duyệt sau N gói (giải phóng RAM)
THROTTLE_SECONDS = 1.5         # nghỉ giữa các gói để giảm tải server
PAGE_SETTLE = 6                # giây chờ trang ổn định sau điều hướng


def setup_logging(name: str = "scraper") -> logging.Logger:
    from logging.handlers import RotatingFileHandler

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    # File có xoay vòng (tránh log phình vô hạn khi chạy nhiều ngày)
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger
