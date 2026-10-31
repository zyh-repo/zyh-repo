#!/usr/bin/env bash
# ZYH 自用 越狱源 —— 独立校验入口（SPEC §5）
# 用法：bash tools/verify.sh [--repo DIR] [--no-http] [--verbose]
# 退出码：0 = 全部通过（可有 SKIP）；1 = 存在 FAIL；2 = 参数/环境错误。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/_python.sh"
exec "$PY" "$HERE/verify.py" "$@"
