"""Chẩn đoán tải TBMT trên server: xem click 'Tải TBMT' xảy ra gì, file về đâu."""
import os
import time
import config as C
from browser import make_driver, dismiss_alert, click_tab, click_span_fileattach, wait_for_text
from selenium.webdriver.common.by import By

# 1 gói bất kỳ đang pending
import sqlite3
c = sqlite3.connect('file:scrape_state.db?mode=ro', uri=True, timeout=5)
row = c.execute("SELECT href, title FROM bids WHERE status='pending' LIMIT 1").fetchone()
href, title = row
print("Gói:", title[:50])

dl = C.DOWNLOAD_DIR
os.makedirs(dl, exist_ok=True)
for f in os.listdir(dl):
    os.remove(os.path.join(dl, f))

d = make_driver(headless=True)
d.set_page_load_timeout(90)
try:
    d.get(href)
    time.sleep(8)
    dismiss_alert(d)
    print("Có nút Tải TBMT:", wait_for_text(d, "Tải TBMT", timeout=30))
    click_tab(d, "Thông báo mời thầu")
    time.sleep(2)
    print("Cửa sổ trước click:", len(d.window_handles))
    # In HTML nút TBMT để hiểu cơ chế
    for el in d.find_elements(By.XPATH, "//span[contains(@class,'tags-fileAttach') and contains(.,'Tải TBMT')]"):
        print("  Nút TBMT HTML:", el.get_attribute("outerHTML")[:200])
        parent = el.find_element(By.XPATH, "..")
        print("  Parent:", parent.get_attribute("outerHTML")[:200])

    home_dl = os.path.expanduser("~/Downloads")
    before = set(os.listdir(dl))
    before_home = set(os.listdir(home_dl)) if os.path.exists(home_dl) else set()
    ok = click_span_fileattach(d, "Tải TBMT")
    print("Click TBMT:", ok)
    found = False
    for i in range(70):
        time.sleep(1)
        new = set(os.listdir(dl)) - before
        new_home = (set(os.listdir(home_dl)) if os.path.exists(home_dl) else set()) - before_home
        wins = len(d.window_handles)
        if new:
            print(f"  t={i}s file mới trong dl: {new}"); found=True; break
        if new_home:
            print(f"  t={i}s file mới trong ~/Downloads: {new_home}"); found=True; break
        if wins > 1:
            print(f"  t={i}s có tab mới ({wins})")
            d.switch_to.window(d.window_handles[-1])
            print("    tab URL:", d.current_url[:120])
            d.switch_to.window(d.window_handles[0])
    if not found:
        print("  KHÔNG có file/tab nào sau 70s")
    print("Cửa sổ cuối:", len(d.window_handles))
    print("dl:", os.listdir(dl), "| ~/Downloads mới:", (set(os.listdir(home_dl))-before_home) if os.path.exists(home_dl) else 'n/a')
finally:
    d.quit()
