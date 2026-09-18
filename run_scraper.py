"""
Hệ thống cào muasamcong.mpi.gov.vn — quy mô lớn, chịu tải, có checkpoint & retry.

Hai pha:
  discover : thu thập danh sách gói thầu 'Chưa đóng thầu' vào DB (SQLite).
  download : tải TBMT + HSMT cho các gói pending/failed, resume được.

Đặc tính chịu tải & bền bỉ:
  - Checkpoint bằng SQLite -> dừng/chạy lại không cào trùng (khử theo notify_id).
  - Retry điều hướng + retry theo gói (đến MAX_ATTEMPTS) -> gói lỗi lâu chuyển 'skipped'.
  - Khởi động lại trình duyệt sau mỗi RESTART_BROWSER_EVERY gói (tránh rò rỉ RAM).
  - Log ra console + scraper.log để biết đang cào tới đâu.
  - Xử lý alert 'Kết nối không ổn định'.

Cách dùng:
  # 1) Thu thập danh sách (vd 600000 gói) rồi tải luôn:
  uv run python run_scraper.py --limit 600000

  # 2) Chỉ thu thập danh sách:
  uv run python run_scraper.py --limit 5000 --discover-only

  # 3) Chỉ tải (dùng danh sách đã thu thập trước đó), resume:
  uv run python run_scraper.py --download-only

  # 4) Xem tiến độ:
  uv run python run_scraper.py --status
"""

from __future__ import annotations

import argparse
import time

import config as C
from browser import (
    click_tab,
    dismiss_alert,
    goto_with_retry,
    make_driver,
    wait_for_text,
)
from db import StateDB
from discovery import discover
from downloader import download_hsmt_webform, download_tbmt
from selenium.webdriver.common.by import By
from utils import sanitize_folder_name

logger = C.setup_logging()


def _has_webform_button(driver, tries: int = 12) -> bool:
    for _ in range(tries):
        spans = driver.find_elements(
            By.XPATH, "//span[contains(normalize-space(.),'biểu mẫu webform')]"
        )
        if any(s.is_displayed() for s in spans):
            return True
        time.sleep(1.5)
    return False


def process_one(driver, row) -> tuple[bool, bool, str]:
    """Xử lý 1 gói: mở chi tiết, tải TBMT & HSMT.

    Trả (tbmt_ok, hsmt_ok, folder_path). Ném Exception nếu lỗi nghiêm trọng.
    """
    href = row["href"]
    title = row["title"] or row["notify_no"] or row["notify_id"]

    if not goto_with_retry(driver, href, logger):
        raise RuntimeError("Không mở được trang chi tiết")
    dismiss_alert(driver)

    if not wait_for_text(driver, "Tải TBMT", timeout=45):
        # có thể trang render chậm hoặc bố cục khác
        dismiss_alert(driver)

    folder = C.OUTPUT_ROOT / sanitize_folder_name(title)
    folder.mkdir(parents=True, exist_ok=True)
    logger.info(f"  -> {title[:70]}")

    # TBMT
    click_tab(driver, "Thông báo mời thầu")
    time.sleep(2)
    tbmt_ok = download_tbmt(driver, folder / "Thông báo mời thầu.pdf", logger)

    # HSMT (webform)
    hsmt_ok = False
    for _ in range(3):
        click_tab(driver, "Hồ sơ mời thầu")
        time.sleep(3)
        if _has_webform_button(driver):
            hsmt_ok = download_hsmt_webform(
                driver, folder / "Hồ sơ mời thầu.pdf", logger
            )
            break
    if not hsmt_ok:
        logger.warning("    HSMT: không có/không tải được biểu mẫu webform")

    return tbmt_ok, hsmt_ok, str(folder)


def run_download(db: StateDB, headless: bool, max_download: int | None = None) -> None:
    stats = db.count_by_status()
    todo = stats.get("pending", 0) + stats.get("failed", 0)
    if max_download:
        todo = min(todo, max_download)
    logger.info(
        f"[DOWNLOAD] Bắt đầu. Sẽ xử lý tối đa {todo} gói. Trạng thái: {stats}"
    )
    # Số gói đã tải THÀNH CÔNG (done) trong lần chạy này
    success_target = max_download

    driver = make_driver(headless=headless)
    processed_since_restart = 0
    consecutive_fails = 0
    done_count = 0

    def restart_browser(nonlocal_driver):
        try:
            nonlocal_driver.quit()
        except Exception:
            pass
        return make_driver(headless=headless)

    try:
        while True:
            # Dừng khi đã đạt đủ số gói done theo yêu cầu
            if success_target is not None:
                done_now = db.count_by_status().get("done", 0)
                if done_now >= success_target:
                    logger.info(
                        f"[DOWNLOAD] Đã đạt {done_now} gói done (>= {success_target}) -> dừng"
                    )
                    break
            rows = db.next_pending(C.MAX_ATTEMPTS, batch=1)
            if not rows:
                break
            row = rows[0]
            nid = row["notify_id"]
            db.mark_in_progress(nid)

            logger.info(f"[{done_count + 1}] {row['notify_no'] or nid}")
            try:
                tbmt_ok, hsmt_ok, folder = process_one(driver, row)
                if tbmt_ok or hsmt_ok:
                    db.mark_done(nid, folder, tbmt_ok, hsmt_ok)
                    consecutive_fails = 0
                else:
                    db.mark_failed(nid, "Không tải được file nào", C.MAX_ATTEMPTS)
                    consecutive_fails += 1
            except Exception as e:  # noqa: BLE001
                logger.error(f"    Lỗi: {str(e)[:120]}")
                dismiss_alert(driver)
                db.mark_failed(nid, str(e), C.MAX_ATTEMPTS)
                consecutive_fails += 1

            done_count += 1
            processed_since_restart += 1
            time.sleep(C.THROTTLE_SECONDS)

            # Khởi động lại trình duyệt khi lỗi liên tiếp (có thể treo/nghẽn)
            if consecutive_fails >= C.MAX_CONSECUTIVE_FAILS:
                logger.warning(
                    f"[DOWNLOAD] {consecutive_fails} lỗi liên tiếp -> khởi động lại trình duyệt"
                )
                driver = restart_browser(driver)
                processed_since_restart = 0
                consecutive_fails = 0
                time.sleep(5)
            # Khởi động lại định kỳ để giải phóng RAM
            elif processed_since_restart >= C.RESTART_BROWSER_EVERY:
                logger.info("[DOWNLOAD] Khởi động lại trình duyệt (giải phóng RAM)")
                driver = restart_browser(driver)
                processed_since_restart = 0

            # In tiến độ định kỳ
            if done_count % 20 == 0:
                logger.info(f"[DOWNLOAD] Tiến độ: {db.count_by_status()}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    logger.info(f"[DOWNLOAD] Kết thúc. Trạng thái cuối: {db.count_by_status()}")


def print_status(db: StateDB) -> None:
    stats = db.count_by_status()
    total = db.total()
    print("=== TIẾN ĐỘ CÀO ===")
    print(f"Tổng gói trong DB : {total}")
    for k in ("pending", "done", "failed", "skipped"):
        print(f"  {k:8s}: {stats.get(k, 0)}")
    print(f"Trang discovery cuối: {db.get_meta('discovery_last_page', '-')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hệ thống cào muasamcong (quy mô lớn)")
    ap.add_argument("--limit", type=int, default=11,
                    help="Số gói cần thu thập ở pha discovery")
    ap.add_argument("--discover-only", action="store_true",
                    help="Chỉ thu thập danh sách, không tải")
    ap.add_argument("--download-only", action="store_true",
                    help="Chỉ tải (dùng danh sách đã có), resume")
    ap.add_argument("--status", action="store_true", help="In tiến độ rồi thoát")
    ap.add_argument("--show", action="store_true", help="Hiện trình duyệt")
    ap.add_argument(
        "--max-download", type=int, default=None,
        help="Số gói tải THÀNH CÔNG tối đa (mặc định = --limit). Đặt 0 để tải hết DB.",
    )
    args = ap.parse_args()

    db = StateDB(C.DB_PATH)
    C.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        if args.status:
            print_status(db)
            return

        headless = not args.show

        if not args.download_only:
            driver = make_driver(headless=headless)
            try:
                discover(driver, db, logger, target=args.limit)
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass

        if not args.discover_only:
            # Mặc định tải đúng số lượng yêu cầu (--limit); --max-download=0 -> tải hết
            if args.max_download is None:
                max_dl = args.limit
            else:
                max_dl = args.max_download or None
            run_download(db, headless=headless, max_download=max_dl)

        print_status(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
