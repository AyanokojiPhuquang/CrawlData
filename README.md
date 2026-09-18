# CrawlData — Cào dữ liệu đấu thầu muasamcong.mpi.gov.vn

Trình cào tự động tải **Thông báo mời thầu (TBMT)** và **Hồ sơ mời thầu (HSMT)**
của các gói thầu ở tab **"Chưa đóng thầu"** trên
[Hệ thống mạng đấu thầu quốc gia](https://muasamcong.mpi.gov.vn/web/guest/contractor-selection?render=index).

Với mỗi gói thầu, chương trình tạo một thư mục theo tên gói và lưu 2 file PDF vào đó.

## Kiến trúc

Thiết kế theo 2 pha, dùng SQLite làm checkpoint để chịu tải lớn (hàng trăm nghìn gói)
và có thể dừng/chạy lại mà không cào trùng.

| File | Vai trò |
|------|---------|
| `config.py`   | Cấu hình tập trung (timeout, số lần retry, throttle, đường dẫn) |
| `db.py`       | Sổ theo dõi trạng thái bằng SQLite (checkpoint, khử trùng theo `notify_id`) |
| `browser.py`  | Khởi tạo Firefox (Selenium) + xử lý TLS, alert, các thao tác DOM |
| `downloader.py` | Tải TBMT và HSMT (qua trình xem biểu mẫu webform) |
| `discovery.py`  | Pha 1 — thu thập danh sách gói thầu, lưu vào DB |
| `run_scraper.py`| Điều phối chính (pha 2 tải file) + giao diện dòng lệnh |

### Vì sao dùng Firefox (không phải Chromium/requests)?

Server muasamcong dùng khóa Diffie-Hellman yếu trong TLS. OpenSSL/BoringSSL đời mới
(dùng bởi `requests` và Chromium) từ chối bắt tay và bị **reset kết nối**. Firefox cho
phép bật lại cipher DHE cũ qua `about:config`, nên kết nối ổn định. Ngoài ra trang render
bằng JavaScript và có reCAPTCHA nên cần trình duyệt thật.

## Yêu cầu hệ thống

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) để quản lý môi trường
- **Firefox** và **geckodriver** cài sẵn trên máy

Mặc định đường dẫn trong `config.py` trỏ tới bản Firefox snap của Ubuntu:

```python
GECKODRIVER = "/snap/bin/geckodriver"
FIREFOX_BIN = "/snap/firefox/current/usr/lib/firefox/firefox"
```

Nếu server dùng đường dẫn khác, hãy sửa 2 biến này (xem `which firefox`, `which geckodriver`).

## Cài đặt

```bash
git clone https://github.com/AyanokojiPhuquang/CrawlData.git
cd CrawlData
uv sync                 # tạo môi trường & cài dependencies từ uv.lock
```

Cài Firefox + geckodriver (nếu server chưa có), ví dụ trên Ubuntu:

```bash
sudo snap install firefox
sudo snap install geckodriver     # hoặc tải từ trang mozilla/geckodriver
```

## Lọc theo khoảng thời gian đăng tải

Mặc định chương trình lọc gói thầu theo **Thời gian đăng tải** bằng tính năng
"Tìm kiếm nâng cao" của trang. Cấu hình trong `config.py`:

```python
PUBLISH_DATE_FROM = "01/01/2026"   # dd/mm/yyyy; đặt None để không lọc
PUBLISH_DATE_TO   = "31/01/2026"
TARGET_TAB        = "all"          # 'all' | 'open' (chưa đóng) | 'closed' (đã đóng)
```

Ví dụ trên sẽ cào các gói đăng tải trong tháng 1/2026 (~7.000 gói), tab "Tất cả".
Nếu đặt `PUBLISH_DATE_FROM = None`, chương trình quay lại chế độ cũ (tab "Chưa đóng thầu").

## Sử dụng

```bash
# Cào 40 gói (thu thập danh sách + tải, dừng khi đủ 40 gói thành công)
uv run python run_scraper.py --limit 40

# Cào quy mô lớn: thu thập 600.000 gói rồi tải hết (resume được nếu bị ngắt)
uv run python run_scraper.py --limit 600000 --max-download 0

# Chỉ thu thập danh sách vào DB (chưa tải)
uv run python run_scraper.py --limit 600000 --discover-only

# Chỉ tải (dùng danh sách đã thu thập), tự resume từ checkpoint
uv run python run_scraper.py --download-only --max-download 0

# Xem tiến độ hiện tại
uv run python run_scraper.py --status

# Hiện cửa sổ trình duyệt để theo dõi (mặc định chạy ẩn/headless)
uv run python run_scraper.py --limit 10 --show
```

## Chạy song song nhiều worker (tăng tốc)

Sau khi đã discovery xong (DB có các gói `pending`), chạy nhiều worker tải song song.
Mỗi worker là 1 tiến trình độc lập, cùng "claim" gói từ SQLite theo cơ chế atomic nên
**không bao giờ tải trùng**. Worker crash sẽ tự khởi động lại; gói đang dở (`in_progress`)
quá 15 phút sẽ được tự động đưa về `pending` để retry.

```bash
# Thu thập danh sách trước
uv run python run_scraper.py --limit 7000 --discover-only

# Chạy 2 worker tải song song (điều chỉnh theo RAM: mỗi Firefox ~600-700MB)
chmod +x run_workers.sh stop_workers.sh
./run_workers.sh 2

# Theo dõi tiến độ (từ máy khác / terminal khác)
uv run python run_scraper.py --status
tail -f logs/worker_w1.log

# Dừng tất cả
./stop_workers.sh
```

### Đường dẫn Firefox/geckodriver

Ghi đè qua biến môi trường nếu server không dùng snap:

```bash
export FIREFOX_BIN=/usr/bin/firefox
export GECKODRIVER_PATH=/usr/local/bin/geckodriver
```

Nếu không đặt, chương trình tự tìm `firefox`/`geckodriver` trong PATH.

## Chạy production dài ngày (bền bỉ)

Hệ thống được thiết kế để chạy liên tục nhiều ngày mà không cần giám sát:

- **Mỗi worker có thư mục download riêng** (`~/dl_w1`, `~/dl_w2`...) → không nhặt nhầm
  file của nhau; file rác `.part` được dọn trước mỗi lần tải.
- **Claim atomic** (SQLite `BEGIN IMMEDIATE`) → không worker nào tải trùng gói.
- **Tự phục hồi mọi lỗi**: lỗi DB, lỗi tạo trình duyệt, geckodriver treo, mất kết nối —
  worker chờ & thử lại, không chết vĩnh viễn.
- **Chống rò rỉ RAM**: khởi động lại trình duyệt mỗi 25 gói + dọn tiến trình firefox
  mồ côi; **watchdog RAM** tạm dừng khi RAM khả dụng < 250MB (tránh OOM trên máy
  không swap).
- **Reclaim gói crash**: gói `in_progress` quá 30 phút được đưa về `pending` để retry.
- **Log xoay vòng** (20MB × 5 file) → không phình đĩa.

Chạy nền, sống sót khi ngắt SSH (dùng `setsid` để tách khỏi phiên SSH):

```bash
cd ~/CrawlData
setsid bash -c './run_workers.sh 2 > logs/workers_main.log 2>&1' < /dev/null &>/dev/null &
```

Kiểm tra tiến độ bất cứ lúc nào (kết nối SSH mới rồi chạy):

```bash
cd ~/CrawlData
./check_progress.sh            # xem tổng quan: %, đủ/thiếu, tốc độ, ETA
tail -f logs/worker_w1.log     # xem log realtime worker 1
```

Dừng:

```bash
./stop_workers.sh
```

> Worker chạy qua `setsid` nên **không bị dừng khi ngắt SSH**. Nếu server reboot,
> chạy lại lệnh `setsid ... run_workers.sh` — hệ thống tự resume từ checkpoint,
> không tải trùng gói đã xong.

> Số worker khuyến nghị theo RAM (mỗi Firefox ~500-700MB, không swap):
> 4GB → 2 worker · 8GB → 3-4 worker · 16GB → 6-8 worker.

### Tham số

| Tham số | Ý nghĩa |
|---------|---------|
| `--limit N`        | Số gói cần thu thập ở pha discovery (và mặc định là số gói tải thành công) |
| `--max-download N` | Số gói tải **thành công** tối đa. Đặt `0` để tải hết những gì có trong DB |
| `--discover-only`  | Chỉ thu thập danh sách, không tải |
| `--download-only`  | Chỉ tải (bỏ qua discovery), resume từ DB |
| `--status`         | In tiến độ rồi thoát |
| `--show`           | Hiện trình duyệt (không headless) |

## Kết quả

```
data/
├── <Tên gói thầu 1>/
│   ├── Thông báo mời thầu.pdf
│   └── Hồ sơ mời thầu.pdf
├── <Tên gói thầu 2>/
│   └── ...
```

- Trạng thái cào lưu trong `scrape_state.db` (SQLite). Xoá file này để cào lại từ đầu.
- Log ghi ra `scraper.log` và console.

## Cơ chế chịu tải & độ bền

- **Checkpoint SQLite**: mỗi gói có trạng thái `pending / done / failed / skipped`;
  chạy lại chỉ xử lý gói chưa xong, khử trùng theo `notify_id`.
- **Retry**: gói lỗi được thử lại đến `MAX_ATTEMPTS` lần, quá số lần thì `skipped`.
- **Retry điều hướng**: tự thử lại khi trang timeout / server chập chờn.
- **Khởi động lại trình duyệt** định kỳ và khi gặp nhiều lỗi liên tiếp (giải phóng RAM,
  tránh treo phiên).
- **Throttle** giữa các gói để giảm tải server.

Tinh chỉnh các ngưỡng này trong `config.py`.

## Lưu ý

- Một số gói không có "biểu mẫu webform" nên chỉ tải được TBMT — đây là trường hợp
  hợp lệ, gói vẫn được đánh dấu hoàn tất khi có ít nhất một tài liệu.
- Chạy trên server nên dùng chế độ headless mặc định. Với server không có màn hình,
  Firefox headless hoạt động bình thường.
