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
ORIG_HOME="$HOME"                       # HOME gốc (chứa uv, repo)
export ORIG_HOME
export PATH="$HOME/.local/bin:$PATH"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Đường dẫn Firefox/geckodriver (server dùng .deb + binary). Ghi đè nếu cần.
export FIREFOX_BIN="${FIREFOX_BIN:-/usr/bin/firefox}"
export GECKODRIVER_PATH="${GECKODRIVER_PATH:-/usr/local/bin/geckodriver}"

mkdir -p logs

echo "Khởi chạy $N worker (Firefox=$FIREFOX_BIN, geckodriver=$GECKODRIVER_PATH)..."
for i in $(seq 1 "$N"); do
  WID="w$i"
  # HOME riêng cho mỗi worker -> ~/Downloads của mỗi worker tách biệt.
  # (Firefox tải PDF mở bằng window.open luôn về $HOME/Downloads, bỏ qua pref dir.)
  WHOME="$HOME/worker_home_$WID"
  DLDIR="$WHOME/Downloads"
  mkdir -p "$DLDIR"
  rm -f "$DLDIR"/* 2>/dev/null
  (
    export HOME="$WHOME"
    export WORKER_DOWNLOAD_DIR="$DLDIR"
    export PATH="$ORIG_HOME/.local/bin:$PATH"
    cd "$PROJECT_DIR" || exit 1
    while true; do
      echo "[$(date '+%F %T')] Khởi động worker $WID (HOME=$WHOME, dl=$DLDIR)"
      uv run python run_scraper.py --download-only --max-download 0 --worker-id "$WID" \
        >> "$PROJECT_DIR/logs/worker_$WID.log" 2>&1
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
