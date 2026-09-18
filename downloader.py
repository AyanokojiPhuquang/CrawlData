"""Xử lý tải file: theo dõi thư mục Downloads, chờ tải xong, di chuyển vào đích."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config as C
from browser import click_span_fileattach, close_extra_tabs


def _snapshot(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()}


def wait_for_new_download(before: set[str], timeout: int = None) -> Path | None:
    """Chờ 1 file mới (không phải .part) xuất hiện & ổn định trong thư mục Downloads."""
    timeout = timeout or C.DOWNLOAD_TIMEOUT
    end = time.time() + timeout
    while time.time() < end:
        current = _snapshot(C.DOWNLOAD_DIR)
        new = [n for n in current - before if not n.endswith(".part")]
        downloading = any(n.endswith(".part") for n in current)
        if new and not downloading:
            newest = max(
                (C.DOWNLOAD_DIR / n for n in new), key=lambda p: p.stat().st_mtime
            )
            size1 = newest.stat().st_size
            time.sleep(1.0)
            if newest.exists() and newest.stat().st_size == size1 and size1 > 0:
                return newest
        time.sleep(1.0)
    return None


def _move_to(downloaded: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if downloaded.suffix and downloaded.suffix.lower() != dest.suffix.lower():
        dest = dest.with_suffix(downloaded.suffix)
    shutil.move(str(downloaded), str(dest))
    return dest


def download_tbmt(driver, dest: Path, logger) -> bool:
    """Tải 'Tải TBMT' (mở tab PDF -> Firefox tự tải về Downloads)."""
    main = driver.current_window_handle
    before = _snapshot(C.DOWNLOAD_DIR)
    if not click_span_fileattach(driver, "Tải TBMT"):
        logger.warning("    Không thấy nút 'Tải TBMT'")
        return False
    got = wait_for_new_download(before, timeout=C.TBMT_TIMEOUT)
    close_extra_tabs(driver, main)
    if not got:
        logger.warning("    TBMT tải không xong (timeout)")
        return False
    final = _move_to(got, dest)
    logger.info(f"    [OK] TBMT -> {final.name}")
    return True


def download_hsmt_webform(driver, dest: Path, logger) -> bool:
    """Tải HSMT: click 'Tải tất cả biểu mẫu webform' -> tab viewer -> 'Tải về'."""
    main = driver.current_window_handle
    handles_before = set(driver.window_handles)
    before = _snapshot(C.DOWNLOAD_DIR)

    if not click_span_fileattach(driver, "biểu mẫu webform"):
        logger.warning("    Không thấy nút 'Tải tất cả biểu mẫu webform'")
        return False

    viewer = None
    for _ in range(20):
        time.sleep(1)
        new = set(driver.window_handles) - handles_before
        if new:
            viewer = new.pop()
            break
    if not viewer:
        logger.warning("    Viewer webform không mở")
        return False

    driver.switch_to.window(viewer)
    ok = False
    try:
        WebDriverWait(driver, C.VIEWER_RENDER_TIMEOUT).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(normalize-space(.),'Tải về')]")
            )
        )
        ok = True
    except TimeoutException:
        pass
    time.sleep(2)

    if ok:
        for btn in driver.find_elements(
            By.XPATH, "//button[contains(normalize-space(.),'Tải về')]"
        ):
            try:
                driver.execute_script("arguments[0].click();", btn)
                break
            except Exception:
                continue

    got = wait_for_new_download(before)
    close_extra_tabs(driver, main)
    if not got:
        logger.warning("    HSMT (webform) tải không xong")
        return False
    final = _move_to(got, dest)
    logger.info(f"    [OK] HSMT -> {final.name}")
    return True
