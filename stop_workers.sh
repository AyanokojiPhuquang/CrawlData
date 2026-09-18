#!/usr/bin/env bash
# Dừng tất cả worker và trình duyệt liên quan.
pkill -f "run_scraper.py" 2>/dev/null
pkill -f "firefox" 2>/dev/null
pkill -f "geckodriver" 2>/dev/null
echo "Đã gửi tín hiệu dừng tới các worker."
