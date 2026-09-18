"""Pha 1 - Thu thập danh sách gói thầu 'Chưa đóng thầu' và lưu vào DB.

Có thể chạy nhiều lần: gói đã có trong DB sẽ bỏ qua (khử trùng theo notify_id).
Ghi meta 'discovery_last_page' để biết đã duyệt tới trang nào.
"""

from __future__ import annotations

import time

from selenium.webdriver.common.by import By

import config as C
from browser import (
    apply_advanced_date_filter,
    dismiss_alert,
    goto_with_retry,
    select_result_tab,
    set_page_size,
)
from db import StateDB
from utils import extract_notify_id, extract_notify_no


def _links_on_page(driver) -> list[tuple[str, str]]:
    """Lấy (title, href) các gói bằng JS một lượt để tránh StaleElementReference."""
    try:
        data = driver.execute_script(
            """
            const out = [];
            const seen = new Set();
            document.querySelectorAll("a[href*='render=detail']").forEach(a => {
                const href = a.href || "";
                const title = (a.textContent || "").trim();
                if (href && !seen.has(href)) { seen.add(href); out.push([title, href]); }
            });
            return out;
            """
        )
        return [(t, h) for t, h in (data or [])]
    except Exception:
        # fallback an toàn nếu execute_script lỗi
        out, seen = [], set()
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='render=detail']"):
            try:
                href = a.get_attribute("href") or ""
                title = (a.text or "").strip()
            except Exception:
                continue
            if href and href not in seen:
                seen.add(href)
                out.append((title, href))
        return out


def _go_next_page(driver) -> bool:
    for b in driver.find_elements(By.CSS_SELECTOR, "button.btn-next"):
        if b.get_attribute("disabled") is None and b.is_enabled():
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", b)
                return True
            except Exception:
                continue
    return False


def discover(driver, db: StateDB, logger, target: int) -> int:
    """Duyệt phân trang, lưu tối đa 'target' gói mới vào DB. Trả số gói mới thêm.

    Nếu cấu hình PUBLISH_DATE_FROM/TO thì lọc theo khoảng ngày đăng tải qua
    tìm kiếm nâng cao, rồi cào tab TARGET_TAB. Ngược lại dùng tab 'Chưa đóng thầu'.
    """
    logger.info(f"[DISCOVERY] Mục tiêu thu thập ~{target} gói")

    if C.PUBLISH_DATE_FROM and C.PUBLISH_DATE_TO:
        logger.info(
            f"[DISCOVERY] Lọc ngày đăng tải {C.PUBLISH_DATE_FROM} - {C.PUBLISH_DATE_TO}, "
            f"tab '{C.TARGET_TAB}'"
        )
        if not apply_advanced_date_filter(
            driver, C.PUBLISH_DATE_FROM, C.PUBLISH_DATE_TO, logger
        ):
            logger.error("[DISCOVERY] Áp dụng bộ lọc ngày thất bại")
            return 0
        select_result_tab(driver, C.TARGET_TAB)
    else:
        if not goto_with_retry(driver, C.LIST_URL, logger):
            logger.error("[DISCOVERY] Không mở được trang danh sách")
            return 0
        time.sleep(C.PAGE_SETTLE + 2)
        dismiss_alert(driver)
        select_result_tab(driver, "open")

    set_page_size(driver, C.PAGE_SIZE)

    added_total = 0
    page = 1
    max_pages = 100000
    no_link_streak = 0  # số trang liên tiếp KHÔNG có link nào (hết dữ liệu)
    start_count = db.total()

    # Dừng khi TỔNG gói trong DB đạt target (hỗ trợ resume: gói cũ vẫn tính)
    while db.total() < target and page < max_pages:
        try:
            links = _links_on_page(driver)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DISCOVERY] Lỗi đọc trang {page}: {str(e)[:60]}, thử lại")
            time.sleep(3)
            dismiss_alert(driver)
            links = _links_on_page(driver)
        added_this_page = 0
        for title, href in links:
            nid = extract_notify_id(href)
            if not nid:
                continue
            if db.upsert_bid(nid, extract_notify_no(href), title, href):
                added_this_page += 1
                added_total += 1
        logger.info(
            f"[DISCOVERY] Trang {page}: {len(links)} link, +{added_this_page} mới "
            f"(tổng mới: {added_total})"
        )
        db.set_meta("discovery_last_page", str(page))

        # Chỉ dừng khi trang KHÔNG có link nào (thực sự hết dữ liệu),
        # không dừng vì gói trùng (để resume duyệt qua vùng đã có).
        if len(links) == 0:
            no_link_streak += 1
            if no_link_streak >= 3:
                logger.info("[DISCOVERY] 3 trang liên tục rỗng -> hết dữ liệu, dừng")
                break
        else:
            no_link_streak = 0

        # đủ mục tiêu?
        if db.total() >= target:
            break

        prev_first = links[0][1] if links else None
        if not _go_next_page(driver):
            logger.info("[DISCOVERY] Hết trang")
            break
        page += 1
        # chờ đổi trang
        for _ in range(20):
            time.sleep(1)
            cur = _links_on_page(driver)
            if cur and cur[0][1] != prev_first:
                break
        dismiss_alert(driver)

    logger.info(
        f"[DISCOVERY] Hoàn tất: thêm {added_total} gói mới "
        f"(DB: {start_count} -> {db.total()})"
    )
    return added_total
