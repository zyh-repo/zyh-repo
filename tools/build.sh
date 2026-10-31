#!/usr/bin/env bash
# 重新生成 Packages / Packages.gz|bz2|xz / Release / sileo-featured.json
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/_python.sh"
exec "$PY" "$HERE/build.py" "$@"
