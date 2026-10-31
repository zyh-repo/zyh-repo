#!/usr/bin/env bash
# 本地预览源：./tools/serve.sh [端口]  默认 8080
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PORT="${1:-8080}"
. "$HERE/_python.sh"
IP="$("$PY" - <<'PY' 2>/dev/null || true
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
except Exception:
    pass
finally:
    s.close()
PY
)"
echo "======================================================"
echo " ZYH 自用 源 — 本地预览"
echo " 本机地址 : http://127.0.0.1:${PORT}/"
if [ -n "$IP" ]; then
  echo " 局域网   : http://${IP}:${PORT}/"
  echo " Sileo 加源: http://${IP}:${PORT}/   （手机与电脑需同一 Wi-Fi）"
fi
echo " 按 Ctrl+C 停止"
echo "======================================================"
exec "$PY" -m http.server "$PORT" --bind 0.0.0.0 --directory "$REPO"
