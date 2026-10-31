#!/usr/bin/env bash
# 发布 deb 到源：./tools/publish.sh path/to/pkg.deb [more.deb ...]
# 把 deb 复制进 debs/，然后重建所有索引。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
if [ "$#" -eq 0 ]; then
  echo "用法：$0 path/to/pkg.deb [more.deb ...]" >&2
  exit 2
fi
. "$HERE/_python.sh"
mkdir -p "$REPO/debs"
for deb in "$@"; do
  if [ ! -f "$deb" ]; then
    echo "[错误] 找不到文件：$deb" >&2
    exit 1
  fi
  case "$deb" in
    *.deb) ;;
    *) echo "[错误] 不是 .deb 文件：$deb" >&2; exit 1 ;;
  esac
  if command -v dpkg-deb >/dev/null 2>&1; then
    if ! dpkg-deb -f "$deb" Package >/dev/null 2>&1; then
      echo "[错误] dpkg-deb 无法读取，可能不是合法 deb：$deb" >&2
      exit 1
    fi
  else
    echo "[提示] 未找到 dpkg-deb，跳过 deb 合法性检查。"
  fi
  cp -f "$deb" "$REPO/debs/"
  echo "[发布] $(basename "$deb") → debs/"
done
exec "$PY" "$HERE/build.py" --verbose
