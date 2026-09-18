#!/usr/bin/env bash
# Xem nhanh tiến độ cào. Dùng: ./check_progress.sh
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")" || exit 1

echo "========================================"
echo " TIẾN ĐỘ CÀO  ($(date '+%F %T'))"
echo "========================================"

# Worker & tài nguyên
NW=$(pgrep -c -f "run_scraper.py --download-only" 2>/dev/null || echo 0)
NF=$(pgrep -c firefox 2>/dev/null || echo 0)
echo "Worker đang chạy: $NW  |  Firefox: $NF"
free -h | awk 'NR==1||/Mem|Swap/{print "  "$0}'
echo "----------------------------------------"

# Trạng thái DB (đọc read-only, không khoá worker)
timeout 25 uv run python -c "
import sqlite3, time
c=sqlite3.connect('file:scrape_state.db?mode=ro',uri=True,timeout=5)
st=dict(c.execute('SELECT status,COUNT(*) FROM bids GROUP BY status').fetchall())
total=sum(st.values())
done=st.get('done',0); skipped=st.get('skipped',0)
full=c.execute(\"SELECT COUNT(*) FROM bids WHERE status='done' AND tbmt_ok=1 AND hsmt_ok=1\").fetchone()[0]
processed=done+skipped
print(f'Tổng gói      : {total}')
print(f'  pending     : {st.get(\"pending\",0)}')
print(f'  in_progress : {st.get(\"in_progress\",0)}')
print(f'  done        : {done}   (đủ 2 file: {full} | thiếu: {done-full})')
print(f'  skipped     : {skipped}')
print(f'Đã xử lý      : {processed}/{total} ({100*processed/total:.1f}%)')
# Tốc độ 30 phút gần nhất
now=time.time()
rows=[r[0] for r in c.execute(\"SELECT updated_at FROM bids WHERE status IN ('done','skipped')\")]
recent=[t for t in rows if now-t<1800]
if len(recent)>=2:
    span=max(recent)-min(recent)
    rate=len(recent)/ (span/3600) if span>0 else 0
    remain=st.get('pending',0)+st.get('in_progress',0)
    eta_h = remain/rate if rate>0 else 0
    print(f'Tốc độ (30ph) : {rate:.0f} gói/giờ  ->  còn ~{eta_h:.1f} giờ ({eta_h/24:.1f} ngày)')
" 2>&1
echo "========================================"
echo "Log realtime: tail -f logs/worker_w1.log"
