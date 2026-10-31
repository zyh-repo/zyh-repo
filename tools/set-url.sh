#!/usr/bin/env bash
# 设置源托管地址并重建：./tools/set-url.sh "https://<user>.github.io/<repo>"
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -ne 1 ]; then
  echo "用法：$0 \"https://<user>.github.io/<repo>\"" >&2
  exit 2
fi
. "$HERE/_python.sh"
exec "$PY" "$HERE/build.py" --set-base-url "$1" --verbose
