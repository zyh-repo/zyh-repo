#!/usr/bin/env bash
# 探测可用的 Python 3 解释器，source 之后使用 $PY。
# 顺序：$PYTHON → 系统 python3 → 宿主 App 自带 python-runtime（由 dpkg-deb 位置反推）
PY="${PYTHON:-}"
if [ -n "$PY" ] && ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  PY=""
fi
if [ -z "$PY" ] && command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
fi
if [ -z "$PY" ]; then
  _DPKG="$(command -v dpkg-deb 2>/dev/null || true)"
  if [ -n "$_DPKG" ]; then
    _APP="$(cd "$(dirname "$_DPKG")/../../.." 2>/dev/null && pwd || true)"
    if [ -n "$_APP" ] && [ -x "$_APP/python-runtime/python3" ]; then
      PY="$_APP/python-runtime/python3"
    fi
  fi
fi
if [ -z "$PY" ]; then
  echo "[错误] 找不到 python3。可执行 PYTHON=/path/to/python3 后重试。" >&2
  exit 1
fi
