#!/usr/bin/env bash
# Chạy song song N worker tải file. Mỗi worker là 1 tiến trình độc lập:
#   - Cùng claim gói từ SQLite (atomic) nên KHÔNG trùng.
#   - Có thư mục download RIÊNG (tránh nhặt nhầm file của worker khác).
#   - Tự khởi động lại nếu crash.
#
# Dùng:
#   ./run_workers.sh 2         # chạy 2 worker (mặc định 2)
#
# Dừng: ./stop_workers.sh

set -u
N="${1:-2}"
export PATH="$HOME/.local/bin:$PATH"

# Đường dẫn Firefox/geckodriver (server dùng .deb + binary). Ghi đè nếu cần.
export FIREFOX_BIN="${FIREFOX_BIN:-/usr/bin/firefox}"
export GECKODRIVER_PATH="${GECKODRIVER_PATH:-/usr/local/bin/geckodriver}"

mkdir -p logs

echo "Khởi chạy $N worker (Firefox=$FIREFOX_BIN, geckodriver=$GECKODRIVER_PATH)..."
for i in $(seq 1 "$N"); do
  WID="w$i"
  DLDIR="$HOME/dl_$WID"          # thư mục download riêng cho worker
  mkdir -p "$DLDIR"
  rm -f "$DLDIR"/* 2>/dev/null   # dọn sạch trước khi chạy
  (
    export WORKER_DOWNLOAD_DIR="$DLDIR"
    while true; do
      echo "[$(date '+%F %T')] Khởi động worker $WID (dl=$DLDIR)"
      uv run python run_scraper.py --download-only --max-download 0 --worker-id "$WID" \
        >> "logs/worker_$WID.log" 2>&1
      code=$?
      if [ $code -eq 0 ]; then
        echo "[$(date '+%F %T')] Worker $WID kết thúc bình thường (hết gói)"
        break
      fi
      echo "[$(date '+%F %T')] Worker $WID thoát code=$code, khởi động lại sau 15s"
      sleep 15
    done
  ) &
  sleep 10   # lệch thời điểm khởi động để tránh mở Firefox đồng loạt (đỡ dồn RAM)
done

wait
echo "Tất cả worker đã kết thúc."
