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
    before = set(os.listdir(dl))
    ok = click_span_fileattach(d, "Tải TBMT")
    print("Click TBMT:", ok)
    # theo dõi 60s xem file về + cửa sổ
    for i in range(60):
        time.sleep(1)
        files = set(os.listdir(dl))
        new = files - before
        if new:
            print(f"  t={i}s file mới: {new}")
            break
    print("Cửa sổ sau click:", len(d.window_handles))
    for h in d.window_handles[1:]:
        d.switch_to.window(h)
        print("  tab mới URL:", d.current_url[:110])
    print("File cuối trong dl:", os.listdir(dl))
finally:
    d.quit()
