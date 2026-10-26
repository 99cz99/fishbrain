#!/system/bin/sh
cd /data/data/com.termux/files/home/dfy || exit 1
# pid 守卫：已有 supervisor 在跑就退出。校验 cmdline 防 stale pid（旧 pid 被复用也不会误判）
[ -f .fish.pid ] && grep -qs run-fish.sh "/proc/$(cat .fish.pid)/cmdline" 2>/dev/null && exit 0
echo $$ > .fish.pid
while true; do
  /data/data/com.termux/files/usr/bin/python -u server.py >> server.log 2>&1
  sleep 2
done
