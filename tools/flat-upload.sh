#!/usr/bin/env bash
# 生成「扁平上传包」：所有文件放同一层，网页上传时不用建文件夹。
# 用法：bash tools/flat-upload.sh [输出目录]
#       bash tools/flat-upload.sh --zip          # 同时在输出目录旁生成 .zip
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
. "$HERE/_python.sh"

OUT=""
ZIP=0
for arg in "$@"; do
  case "$arg" in
    --zip) ZIP=1 ;;
    -h | --help)
      sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      if [ -z "$OUT" ]; then OUT="$arg"; else echo "[错误] 多余参数：$arg" >&2; exit 2; fi
      ;;
  esac
done

if [ -z "$OUT" ]; then
  OUT="$(cd "$REPO/.." && pwd)/ZYH-源部署包"
fi

ARGS=(--repo "$REPO" --out "$OUT")
if [ "$ZIP" -eq 1 ]; then
  ARGS+=(--zip)
fi
exec "$PY" "$HERE/flat_upload.py" "${ARGS[@]}"
