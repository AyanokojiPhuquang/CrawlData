#!/usr/bin/env bash
# Dừng tất cả worker và trình duyệt liên quan (triệt để).
pkill -f "run_workers.sh" 2>/dev/null
pkill -f "run_scraper.py" 2>/dev/null
sleep 2
pkill -9 -f "firefox" 2>/dev/null       # bắt cả firefox-bin
pkill -9 -f "geckodriver" 2>/dev/null
sleep 1
# Dọn thư mục download tạm của các worker
rm -rf "$HOME"/dl_w* 2>/dev/null
rm -rf "$HOME"/worker_home_w*/Downloads/* 2>/dev/null
echo "Đã dừng worker. Firefox còn lại: $(pgrep -c firefox 2>/dev/null || echo 0)"
