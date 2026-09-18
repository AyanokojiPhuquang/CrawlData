"""Cấu hình tập trung cho hệ thống cào."""
from __future__ import annotations

import logging
from pathlib import Path

# --- Đường dẫn ---
BASE_URL = "https://muasamcong.mpi.gov.vn"
LIST_URL = f"{BASE_URL}/web/guest/contractor-selection?render=index"

GECKODRIVER = "/snap/bin/geckodriver"
FIREFOX_BIN = "/snap/firefox/current/usr/lib/firefox/firefox"

OUTPUT_ROOT = Path("data")
DOWNLOAD_DIR = Path.home() / "Downloads"  # Firefox tải về đây (do site dùng blob)
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
RESTART_BROWSER_EVERY = 40     # khởi động lại trình duyệt sau N gói (tránh rò rỉ RAM)
THROTTLE_SECONDS = 1.5         # nghỉ giữa các gói để giảm tải server
PAGE_SETTLE = 6                # giây chờ trang ổn định sau điều hướng


def setup_logging(name: str = "scraper") -> logging.Logger:
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
    # File (append để giữ lịch sử qua các lần chạy)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger
