"""Pha 1 - Thu thập danh sách gói thầu 'Chưa đóng thầu' và lưu vào DB.

Có thể chạy nhiều lần: gói đã có trong DB sẽ bỏ qua (khử trùng theo notify_id).
Ghi meta 'discovery_last_page' để biết đã duyệt tới trang nào.
"""

from __future__ import annotations

import time

from selenium.webdriver.common.by import By

import config as C
from browser import (
    dismiss_alert,
    goto_with_retry,
    set_page_size,
)
from db import StateDB
from utils import extract_notify_id, extract_notify_no


def _select_tab_chua_dong_thau(driver) -> None:
    for a in driver.find_elements(By.CSS_SELECTOR, "a[data-toggle='tab']"):
        if "Chưa đóng thầu" in (a.text or ""):
            driver.execute_script("arguments[0].click();", a)
            break
    time.sleep(C.PAGE_SETTLE)


def _links_on_page(driver) -> list[tuple[str, str]]:
    out, seen = [], set()
    for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='render=detail']"):
        href = a.get_attribute("href") or ""
        title = (a.text or "").strip()
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
    """Duyệt phân trang, lưu tối đa 'target' gói mới vào DB. Trả số gói mới thêm."""
    logger.info(f"[DISCOVERY] Mục tiêu thu thập ~{target} gói")
    if not goto_with_retry(driver, C.LIST_URL, logger):
        logger.error("[DISCOVERY] Không mở được trang danh sách")
        return 0
    time.sleep(C.PAGE_SETTLE + 2)
    dismiss_alert(driver)
    _select_tab_chua_dong_thau(driver)
    set_page_size(driver, C.PAGE_SIZE)

    added_total = 0
    page = 1
    max_pages = 100000
    empty_streak = 0

    while db.count_by_status().get("pending", 0) + added_total < target and page < max_pages:
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

        if added_this_page == 0:
            empty_streak += 1
            if empty_streak >= 3:
                logger.info("[DISCOVERY] 3 trang liên tục không có gói mới -> dừng")
                break
        else:
            empty_streak = 0

        # đủ mục tiêu?
        pending_now = db.count_by_status().get("pending", 0)
        if pending_now >= target:
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

    logger.info(f"[DISCOVERY] Hoàn tất: thêm {added_total} gói mới vào DB")
    return added_total
