#!/usr/bin/env bash
# ZYH 自用 越狱源 —— 手机端一键发布插件
#
# 一条命令完成：校验 deb → 拷进 debs/（可选设置图标）→ 重建索引 → 运行校验
#               → 生成「源部署包」→ 对比上次快照，列出这次需要上传到 GitHub 的文件。
#
# 用法：
#   bash tools/phone-publish.sh 插件.deb [更多.deb ...]
#   bash tools/phone-publish.sh --icon 图标.png 插件.deb      # 同时设置插件图标（256×256 PNG）
#   bash tools/phone-publish.sh --init                        # 首次部署后：记录当前已上传文件的快照
#   bash tools/phone-publish.sh --full 插件.deb               # 强制列出部署包里的全部文件（忽略快照）
#
# 发布完成后：GitHub 仓库根 → Add file → Upload files → 选中列出的文件 → Commit changes。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
. "$HERE/_python.sh"

WORKSPACE="$(cd "$REPO/.." && pwd)"
OUT_DIR="$WORKSPACE/ZYH-源部署包"
STATE="$WORKSPACE/ZYH-部署状态.json"

INIT=0
FULL=0
DIFF=0
ICON=""
DEBS=()

die() { echo "[错误] $*" >&2; exit 1; }

usage() {
  sed -n '7,11p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --init) INIT=1 ;;
    --full) FULL=1 ;;
    --diff) DIFF=1 ;;
    --icon)
      shift
      [ "$#" -ge 1 ] || die "--icon 后面需要跟图标 PNG 路径"
      ICON="$1"
      ;;
    --out)
      shift
      [ "$#" -ge 1 ] || die "--out 后面需要跟输出目录"
      OUT_DIR="$1"
      ;;
    --state)
      shift
      [ "$#" -ge 1 ] || die "--state 后面需要跟状态文件路径"
      STATE="$1"
      ;;
    -h | --help) usage; exit 0 ;;
    -*) die "未知参数：$1（用 --help 查看用法）" ;;
    *)
      case "$1" in
        *.deb) DEBS+=("$1") ;;
        *) die "不是 .deb 文件：$1" ;;
      esac
      ;;
  esac
  shift
done

snapshot() {
  "$PY" - "$OUT_DIR" "$STATE" "$FULL" <<'PY'
import hashlib
import json
import os
import sys
import time

bundle, state_path, full = sys.argv[1], sys.argv[2], sys.argv[3] == "1"

if not os.path.isdir(bundle):
    print("[错误] 部署包目录不存在：%s（先运行 bash tools/flat-upload.sh --zip）" % bundle, file=sys.stderr)
    sys.exit(1)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


current = {}
for name in sorted(os.listdir(bundle)):
    path = os.path.join(bundle, name)
    if os.path.isfile(path):
        current[name] = digest(path)

previous = {}
if os.path.isfile(state_path):
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            previous = json.load(fh).get("files", {})
    except (OSError, ValueError):
        previous = {}

added = [n for n in current if n not in previous]
changed = [n for n in current if n in previous and current[n] != previous[n]]
removed = [n for n in previous if n not in current]

print("=" * 66)
print(" 需要上传到 GitHub 的文件（仓库根 → Add file → Upload files 全选）")
print("=" * 66)
if full or not previous:
    print("（%s）" % ("--full：重新列出全部文件" if full and previous else "首次：全部文件"))
    for name in sorted(current):
        print("  [上传] " + name)
else:
    if not (added or changed or removed):
        print("  没有变化，无需上传。")
    for name in added:
        print("  [新增] " + name)
    for name in changed:
        print("  [更新] " + name)
    for name in removed:
        print("  [删除] " + name + "   ← 在 GitHub 仓库里手动删除这个文件")

with open(state_path, "w", encoding="utf-8") as fh:
    json.dump(
        {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "bundle": bundle,
         "files": current},
        fh,
        ensure_ascii=False,
        indent=1,
    )
    fh.write("\n")

print()
print("上传完成后 Sileo 里下拉刷新即可看到新插件。")
print("（本次已记录快照：%s；如果这次没上传，下次请加 --full 重新列全量）" % state_path)
PY
}

if [ "$DIFF" -eq 1 ]; then
  echo "[部署包] 重建：$OUT_DIR"
  bash "$HERE/flat-upload.sh" "$OUT_DIR" --zip >/dev/null
  snapshot
  exit 0
fi

if [ "$INIT" -eq 1 ]; then
  [ -d "$OUT_DIR" ] || die "部署包不存在：$OUT_DIR；先运行 bash tools/flat-upload.sh --zip"
  echo "[初始化] 把当前部署包记录为「已上传」快照……"
  if [ -f "$STATE" ] && [ "${FULL}" -eq 0 ]; then
    echo "[提示] 已存在快照 $STATE，将被覆盖。"
  fi
  FULL=1 snapshot
  exit 0
fi

[ "${#DEBS[@]}" -gt 0 ] || { usage; exit 2; }

# ---------- 1) 校验并拷入 debs/ ----------
mkdir -p "$REPO/debs"
for deb in "${DEBS[@]}"; do
  [ -f "$deb" ] || die "找不到文件：$deb"
  if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb -f "$deb" Package >/dev/null 2>&1 || die "dpkg-deb 读不出这个 deb，可能不是合法包：$deb"
  fi
  cp -f "$deb" "$REPO/debs/"
  echo "[发布] $(basename "$deb") → debs/"
done

# ---------- 2) 可选：设置插件图标 ----------
if [ -n "$ICON" ]; then
  [ -f "$ICON" ] || die "找不到图标文件：$ICON"
  if [ "${#DEBS[@]}" -ne 1 ]; then
    die "--icon 只能在一次发布一个 deb 时使用（避免图标张冠李戴）"
  fi
  DEB_PATH="$REPO/debs/$(basename "${DEBS[0]}")"
  PKG=""
  if command -v dpkg-deb >/dev/null 2>&1; then
    PKG="$(dpkg-deb -f "$DEB_PATH" Package 2>/dev/null || true)"
  fi
  if [ -z "$PKG" ]; then
    PKG="$(basename "${DEBS[0]}")"
    PKG="${PKG%%_*}"
  fi
  mkdir -p "$REPO/icons"
  cp -f "$ICON" "$REPO/icons/$PKG.png"
  echo "[图标] $(basename "$ICON") → icons/$PKG.png"
fi

# ---------- 3) 重建索引 ----------
echo
"$PY" "$HERE/build.py" --verbose

# ---------- 4) 校验 ----------
echo
echo "[校验] 运行 tools/verify.sh ……"
if ! bash "$HERE/verify.sh"; then
  die "校验未通过：请先修好上面的 FAIL 项，再重新发布（部署包未生成，避免上传坏源）"
fi

# ---------- 5) 生成部署包 ----------
echo
echo "[部署包] 生成 ZYH-源部署包 ……"
bash "$HERE/flat-upload.sh" "$OUT_DIR" --zip >/dev/null
echo "[部署包] 完成：$OUT_DIR（以及同名 .zip）"

# ---------- 6) 列上传清单 ----------
echo
snapshot
