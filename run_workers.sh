#!/usr/bin/env bash
# Chạy song song N worker tải file. Mỗi worker là 1 tiến trình độc lập, cùng
# claim gói từ SQLite (không trùng). Worker crash sẽ tự khởi động lại.
#
# Dùng:
#   ./run_workers.sh 2        # chạy 2 worker (mặc định 2)
#
# Dừng tất cả: ./stop_workers.sh  (hoặc pkill -f run_scraper.py)

set -u
N="${1:-2}"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p logs

echo "Khởi chạy $N worker..."
for i in $(seq 1 "$N"); do
  WID="w$i"
  (
    while true; do
      echo "[$(date '+%F %T')] Khởi động worker $WID"
      uv run python run_scraper.py --download-only --max-download 0 --worker-id "$WID" \
        >> "logs/worker_$WID.log" 2>&1
      code=$?
      # Nếu thoát bình thường (hết gói) -> dừng vòng lặp restart
      if [ $code -eq 0 ]; then
        echo "[$(date '+%F %T')] Worker $WID kết thúc bình thường"
        break
      fi
      echo "[$(date '+%F %T')] Worker $WID crash (code=$code), khởi động lại sau 10s"
      sleep 10
    done
  ) &
  # Lệch thời điểm khởi động để tránh tranh chấp lúc mở trình duyệt đồng loạt
  sleep 8
done

wait
echo "Tất cả worker đã kết thúc."
