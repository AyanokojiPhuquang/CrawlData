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
from downloader import cleanup_download_dir, download_hsmt_webform, download_tbmt
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


def _make_driver_resilient(headless: bool, worker_id: str, max_tries: int = 5):
    """Tạo trình duyệt với retry — nếu thất bại (RAM tạm thời thiếu) thì chờ & thử lại."""
    for i in range(max_tries):
        try:
            return make_driver(headless=headless)
        except Exception as e:  # noqa: BLE001
            wait = 15 * (i + 1)
            logger.warning(
                f"[{worker_id}] Tạo trình duyệt lỗi ({str(e)[:70]}), "
                f"dọn tiến trình & chờ {wait}s (thử {i + 1}/{max_tries})"
            )
            _kill_orphan_processes()
            time.sleep(wait)
    return None


def _restart_browser_resilient(driver, headless: bool, worker_id: str):
    """Đóng trình duyệt cũ (kể cả treo) rồi tạo lại. Trả None nếu không tạo được."""
    try:
        driver.quit()
    except Exception:
        pass
    _kill_orphan_processes()  # dọn firefox-bin zombie còn sót
    time.sleep(2)
    return _make_driver_resilient(headless, worker_id)


def _safe_db(fn, *args) -> None:
    """Gọi hàm ghi DB, thử lại vài lần nếu DB bị khoá tạm thời."""
    for i in range(5):
        try:
            fn(*args)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"    Ghi DB lỗi ({str(e)[:60]}), thử lại {i + 1}/5")
            time.sleep(2 * (i + 1))
    logger.error("    Ghi DB thất bại sau nhiều lần thử")


def _available_ram_mb() -> int:
    """Trả RAM khả dụng (MB). Đọc /proc/meminfo, trả -1 nếu không đọc được."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def _wait_for_ram(worker_id: str, min_mb: int = 250, max_wait: int = 120) -> None:
    """Nếu RAM khả dụng thấp, chờ một chút cho worker khác giải phóng (tránh OOM)."""
    waited = 0
    while waited < max_wait:
        avail = _available_ram_mb()
        if avail < 0 or avail >= min_mb:
            return
        logger.warning(
            f"[{worker_id}] RAM thấp ({avail}MB < {min_mb}MB), chờ 10s trước khi tiếp"
        )
        time.sleep(10)
        waited += 10


def _kill_orphan_processes() -> None:
    """Dọn các tiến trình firefox/geckodriver mồ côi của CHÍNH tiến trình này.

    Chỉ kill tiến trình con của process hiện tại để không ảnh hưởng worker khác.
    """
    import os
    import signal
    import subprocess

    try:
        # Lấy các tiến trình con (geckodriver/firefox) do worker này sinh ra
        out = subprocess.run(
            ["pgrep", "-P", str(os.getpid())],
            capture_output=True, text=True, timeout=10,
        )
        for pid in out.stdout.split():
            # kill cả cây tiến trình con
            try:
                subprocess.run(["pkill", "-9", "-P", pid], timeout=10)
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass
    except Exception:
        pass


def run_download(
    db: StateDB,
    headless: bool,
    max_download: int | None = None,
    worker_id: str = "w0",
) -> None:
    # Dọn thư mục download riêng của worker (tránh file rác từ lần trước)
    cleanup_download_dir()
    logger.info(f"[{worker_id}] Thư mục download: {C.DOWNLOAD_DIR}")

    # Khôi phục các gói 'in_progress' quá hạn (do lần chạy trước bị crash)
    recovered = db.reclaim_stale(stale_seconds=900)
    if recovered:
        logger.info(f"[{worker_id}] Khôi phục {recovered} gói in_progress quá hạn")

    stats = db.count_by_status()
    logger.info(f"[{worker_id}] [DOWNLOAD] Bắt đầu. Trạng thái: {stats}")
    # Số gói đã tải THÀNH CÔNG (done) trong lần chạy này (giới hạn theo worker)
    success_target = max_download
    local_done = 0

    driver = _make_driver_resilient(headless, worker_id)
    if driver is None:
        logger.error(f"[{worker_id}] Không khởi tạo được trình duyệt -> thoát")
        return

    processed_since_restart = 0
    consecutive_fails = 0
    done_count = 0
    last_reclaim = time.time()

    try:
        while True:
            # Dừng khi worker này đã tải đủ số gói done theo yêu cầu
            if success_target is not None and local_done >= success_target:
                logger.info(
                    f"[{worker_id}] Đã đạt {local_done} gói (>= {success_target}) -> dừng"
                )
                break

            # Định kỳ reclaim gói in_progress quá hạn (worker khác crash hẳn).
            # Ngưỡng 30 phút: đủ lớn để không giành nhầm gói worker khác đang xử lý.
            if time.time() - last_reclaim > 600:
                try:
                    n = db.reclaim_stale(stale_seconds=1800)
                    if n:
                        logger.info(f"[{worker_id}] Reclaim {n} gói in_progress quá hạn")
                except Exception:
                    pass
                last_reclaim = time.time()

            # Bảo vệ chống OOM: chờ nếu RAM khả dụng quá thấp
            _wait_for_ram(worker_id, min_mb=250)

            # Claim 1 gói atomic (an toàn cho nhiều worker). Bọc lỗi DB.
            try:
                row = db.claim_next(C.MAX_ATTEMPTS, worker_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{worker_id}] Lỗi claim DB: {str(e)[:80]}, chờ 10s")
                time.sleep(10)
                continue
            if row is None:
                logger.info(f"[{worker_id}] Không còn gói -> dừng")
                break
            nid = row["notify_id"]

            logger.info(f"[{worker_id}][{done_count + 1}] {row['notify_no'] or nid}")
            try:
                tbmt_ok, hsmt_ok, folder = process_one(driver, row)
                if tbmt_ok or hsmt_ok:
                    _safe_db(db.mark_done, nid, folder, tbmt_ok, hsmt_ok)
                    consecutive_fails = 0
                    local_done += 1
                else:
                    _safe_db(db.mark_failed, nid, "Không tải được file nào", C.MAX_ATTEMPTS)
                    consecutive_fails += 1
            except Exception as e:  # noqa: BLE001
                logger.error(f"    Lỗi xử lý gói: {str(e)[:120]}")
                try:
                    dismiss_alert(driver)
                except Exception:
                    pass
                _safe_db(db.mark_failed, nid, str(e), C.MAX_ATTEMPTS)
                consecutive_fails += 1

            done_count += 1
            processed_since_restart += 1
            time.sleep(C.THROTTLE_SECONDS)

            # Khởi động lại trình duyệt khi lỗi liên tiếp (treo/nghẽn/mất geckodriver)
            need_restart = consecutive_fails >= C.MAX_CONSECUTIVE_FAILS
            periodic_restart = processed_since_restart >= C.RESTART_BROWSER_EVERY
            if need_restart or periodic_restart:
                reason = "lỗi liên tiếp" if need_restart else "định kỳ giải phóng RAM"
                logger.info(f"[{worker_id}] Khởi động lại trình duyệt ({reason})")
                driver = _restart_browser_resilient(driver, headless, worker_id)
                if driver is None:
                    logger.error(f"[{worker_id}] Không khởi động lại được -> thoát vòng lặp")
                    break
                processed_since_restart = 0
                consecutive_fails = 0
                time.sleep(3)

            if done_count % 20 == 0:
                try:
                    logger.info(f"[{worker_id}] Tiến độ chung: {db.count_by_status()}")
                except Exception:
                    pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        _kill_orphan_processes()

    logger.info(f"[{worker_id}] Kết thúc. Đã tải {local_done} gói trong lần chạy này.")


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
    ap.add_argument(
        "--worker-id", type=str, default="w0",
        help="Định danh worker (khi chạy song song nhiều tiến trình)",
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
            run_download(
                db, headless=headless, max_download=max_dl, worker_id=args.worker_id
            )

        print_status(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
