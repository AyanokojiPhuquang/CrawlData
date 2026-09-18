"""Quản lý trình duyệt Firefox (Selenium) và các thao tác cấp thấp.

Firefox được chọn vì server dùng khóa Diffie-Hellman yếu (Chromium/urllib mới từ chối),
Firefox cho phép bật lại cipher DHE cũ qua prefs.
"""

from __future__ import annotations

import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    NoAlertPresentException,
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

import config as C


def make_driver(headless: bool = True) -> webdriver.Firefox:
    import os

    opts = Options()
    if headless:
        opts.add_argument("-headless")
    # Chỉ set binary_location nếu đường dẫn tồn tại; nếu không, để Selenium tự tìm firefox
    if C.FIREFOX_BIN and os.path.exists(C.FIREFOX_BIN):
        opts.binary_location = C.FIREFOX_BIN
    C.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    prefs = {
        # TLS/cipher cũ để bắt tay với server DH key yếu
        "security.ssl3.dhe_rsa_aes_128_sha": True,
        "security.ssl3.dhe_rsa_aes_256_sha": True,
        "security.tls.version.min": 1,
        "security.tls.version.enable-deprecated": True,
        "security.ssl.require_safe_negotiation": False,
        "security.ssl.treat_unsafe_negotiation_as_broken": False,
        "intl.accept_languages": "vi-VN, vi, en",
        # Tải file tự động về thư mục Downloads mặc định
        "browser.download.folderList": 1,
        "browser.download.manager.showWhenStarting": False,
        "pdfjs.disabled": True,
        "browser.helperApps.neverAsk.saveToDisk": (
            "application/pdf,application/octet-stream,application/zip,"
            "application/x-zip-compressed,application/download,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/msword,application/vnd.ms-excel,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        "browser.download.always_ask_before_handling_new_types": False,
        # Giảm tiêu thụ tài nguyên khi chạy dài
        "browser.cache.disk.enable": False,
        "browser.sessionhistory.max_entries": 3,
    }
    for k, v in prefs.items():
        opts.set_preference(k, v)

    if C.GECKODRIVER and os.path.exists(C.GECKODRIVER):
        service = Service(executable_path=C.GECKODRIVER)
    else:
        service = Service()  # tự tìm geckodriver trong PATH
    driver = webdriver.Firefox(service=service, options=opts)
    driver.set_page_load_timeout(75)
    return driver


def dismiss_alert(driver) -> bool:
    """Đóng alert 'Kết nối không ổn định' nếu có. Trả True nếu đã đóng 1 alert."""
    try:
        alert = driver.switch_to.alert
        alert.accept()
        return True
    except NoAlertPresentException:
        return False
    except Exception:
        return False


def goto_with_retry(driver, url: str, logger, tries: int = None) -> bool:
    tries = tries or C.NAV_RETRIES
    for i in range(tries):
        try:
            driver.get(url)
            return True
        except UnexpectedAlertPresentException:
            dismiss_alert(driver)
            time.sleep(2)
        except TimeoutException:
            logger.warning(f"  Timeout tải trang (thử {i + 1}/{tries})")
            time.sleep(3)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"  Lỗi tải trang: {str(e)[:80]} (thử {i + 1}/{tries})")
            dismiss_alert(driver)
            time.sleep(3)
    return False


def wait_for_text(driver, text: str, timeout: int = 40) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.XPATH, f"//*[contains(normalize-space(.),'{text}')]")
            )
        )
        return True
    except TimeoutException:
        return False


def click_by_text(driver, text: str, tag: str = "*") -> bool:
    """Scroll tới và click phần tử đầu tiên chứa text (dùng JS click)."""
    xp = f"//{tag}[contains(normalize-space(.),'{text}')]"
    for el in driver.find_elements(By.XPATH, xp):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            continue
    return False


def click_span_fileattach(driver, text: str) -> bool:
    """Click span.tags-fileAttach chứa text (nút tải)."""
    xp = (
        f"//span[contains(@class,'tags-fileAttach') and "
        f"contains(normalize-space(.),'{text}')]"
    )
    els = driver.find_elements(By.XPATH, xp)
    if not els:
        els = driver.find_elements(
            By.XPATH, f"//span[contains(normalize-space(.),'{text}')]"
        )
    for el in els:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            continue
    return False


def click_tab(driver, text: str) -> bool:
    for xp in (
        f"//a[@data-toggle='tab' and contains(normalize-space(.),'{text}')]",
        f"//a[contains(normalize-space(.),'{text}')]",
    ):
        for a in driver.find_elements(By.XPATH, xp):
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", a
                )
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", a)
                return True
            except Exception:
                continue
    return False


def close_extra_tabs(driver, main_handle: str) -> None:
    for h in list(driver.window_handles):
        if h != main_handle:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
    try:
        driver.switch_to.window(main_handle)
    except Exception:
        pass


def fill_ant_date(driver, picker_input, value: str) -> str:
    """Điền 1 ô ngày Ant Design: click -> gõ vào .ant-calendar-input -> ENTER."""
    from selenium.webdriver.common.keys import Keys

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", picker_input)
    driver.execute_script("arguments[0].click();", picker_input)
    time.sleep(1.5)
    cals = [
        c
        for c in driver.find_elements(By.CSS_SELECTOR, ".ant-calendar-input")
        if c.is_displayed()
    ]
    if not cals:
        return ""
    ci = cals[0]
    try:
        ci.clear()
    except Exception:
        pass
    ci.send_keys(value)
    time.sleep(1.0)
    ci.send_keys(Keys.ENTER)
    time.sleep(1.0)
    return picker_input.get_attribute("value") or ""


def apply_advanced_date_filter(driver, date_from: str, date_to: str, logger) -> bool:
    """Mở trang tìm kiếm nâng cao, điền khoảng 'Thời gian đăng tải', bấm Tìm kiếm.

    Trả True nếu submit thành công (trang chuyển sang danh sách kết quả).
    """
    if not goto_with_retry(driver, C.SEARCH_URL, logger):
        logger.error("[FILTER] Không mở được trang tìm kiếm nâng cao")
        return False
    time.sleep(C.PAGE_SETTLE + 3)
    dismiss_alert(driver)

    def date_inputs():
        return [
            i
            for i in driver.find_elements(By.XPATH, "//input[@placeholder='dd/mm/yyyy']")
            if i.is_displayed()
        ]

    dins = date_inputs()
    if len(dins) < 2:
        logger.error(f"[FILTER] Không tìm thấy đủ ô ngày (thấy {len(dins)})")
        return False

    # 2 ô đầu = 'Thời gian đăng tải' (Từ / Đến)
    v_from = fill_ant_date(driver, dins[0], date_from)
    dins = date_inputs()  # refresh sau khi DOM đổi
    v_to = fill_ant_date(driver, dins[1], date_to)
    logger.info(f"[FILTER] Thời gian đăng tải: {v_from} -> {v_to}")
    if v_from != date_from or v_to != date_to:
        logger.warning("[FILTER] Giá trị ngày điền vào không khớp mong đợi")

    # Bấm nút 'Tìm kiếm'
    clicked = False
    for b in driver.find_elements(
        By.XPATH, "//button[contains(normalize-space(.),'Tìm kiếm')]"
    ):
        if b.is_displayed():
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
            driver.execute_script("arguments[0].click();", b)
            clicked = True
            break
    if not clicked:
        logger.error("[FILTER] Không thấy nút 'Tìm kiếm'")
        return False

    # Chờ danh sách kết quả xuất hiện
    for _ in range(8):
        time.sleep(5)
        dismiss_alert(driver)
        if driver.find_elements(By.CSS_SELECTOR, "a[href*='render=detail']"):
            logger.info("[FILTER] Đã áp dụng bộ lọc, có kết quả")
            return True
    logger.warning("[FILTER] Submit xong nhưng chưa thấy kết quả")
    return False


def select_result_tab(driver, tab: str) -> None:
    """Chọn tab kết quả: 'all' | 'open' | 'closed'."""
    label = {
        "all": "Tất cả",
        "open": "Chưa đóng thầu",
        "closed": "Đã đóng thầu",
    }.get(tab, "Tất cả")
    for a in driver.find_elements(By.CSS_SELECTOR, "a[data-toggle='tab'], ul.nav a"):
        if label in (a.text or ""):
            try:
                driver.execute_script("arguments[0].click();", a)
                time.sleep(C.PAGE_SETTLE)
                return
            except Exception:
                continue


def set_page_size(driver, size: int = C.PAGE_SIZE) -> bool:
    for sel in driver.find_elements(By.XPATH, "//select"):
        try:
            opts = [o.get_attribute("value") for o in sel.find_elements(By.TAG_NAME, "option")]
            if str(size) in opts:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sel)
                Select(sel).select_by_value(str(size))
                time.sleep(6)
                return True
        except Exception:
            continue
    return False
