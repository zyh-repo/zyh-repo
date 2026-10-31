#!/usr/bin/env bash
# ZYH 自用 越狱源 —— 插件管理工具
# 统一入口：查看源里有哪些插件、看单个插件详情、添加/更新 deb、删除插件、清理旧版本。
# 任何增删都会自动重建 Packages / Packages.gz|bz2|xz / Release（以及 BASE_URL 非空时的 sileo-featured.json）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
. "$HERE/_python.sh"

die() { echo "[错误] $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法：bash tools/manage.sh <子命令> [参数...]

子命令：
  list                        列出源里当前的插件（包名/名称/版本/架构/大小/deb 文件）
  info <Package>              查看某个插件在 Packages 索引里的完整字段
  add <deb> [deb...]          添加 / 发布 deb（拷贝进 debs/ 并自动重建索引）
  remove [-y] <包名或 deb 名>  删除插件（给 Package 名或 debs/ 里的文件名，可多个）
  prune [-y]                  清理未进索引的旧版本 deb（同包同架构只保留最高版本进索引）
  icon <Package>              查看该插件的图标文件路径与状态
  help                        显示本帮助

示例：
  bash tools/manage.sh list
  bash tools/manage.sh info com.example.myplugin
  bash tools/manage.sh add ~/Desktop/NewTweak_1.1_iphoneos-arm64.deb
  bash tools/manage.sh remove com.example.myplugin          # 删该包所有架构
  bash tools/manage.sh remove com.example.myplugin_1.0_iphoneos-arm64.deb
  bash tools/manage.sh prune -y

规则提示：
  - 同一个 Package + Architecture 有多个版本时，只有版本最高的进索引；
    旧 deb 仍在 debs/ 里但 Sileo 看不到，可用 prune 清理。
  - 插件的显示名 / 描述 / 依赖等字段写死在 deb 的 control 里：
    要改这些，需要重新打包 deb，再 manage.sh add。
  - 新增插件想显示图标：放一张 256×256 的 PNG 到 icons/<Package>.png，
    并设置 BASE_URL（./tools/set-url.sh "https://…"），重建后才会写进索引。
EOF
}

inspect() {
  "$PY" - "$REPO" "$@" <<'PY'
import os
import sys
import signal
import unicodedata

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

repo, mode = sys.argv[1], sys.argv[2]
arg = sys.argv[3] if len(sys.argv) > 3 else ""


def parse_packages(path):
    """独立的最小 RFC822 解析（仅用于管理工具展示，不参与构建）。"""
    stanzas, cur, last = [], None, None
    if not os.path.isfile(path):
        return stanzas
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read().replace("\r\n", "\n").replace("\r", "\n")
    for line in text.split("\n"):
        if line == "":
            if cur:
                stanzas.append(cur)
            cur, last = None, None
            continue
        if line.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            if cur is not None and last:
                cur[last].append(line.lstrip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if cur is None:
            cur = {}
        cur[key] = [value.strip()]
        last = key
    if cur:
        stanzas.append(cur)
    return [{k: "\n".join(v) for k, v in s.items()} for s in stanzas]


def disp_width(text):
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text, width):
    return text + " " * max(0, width - disp_width(text))


def human_size(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.2f MB" % (n / (1024.0 * 1024.0))


packages_file = os.path.join(repo, "Packages")
stanzas = parse_packages(packages_file)
debs_dir = os.path.join(repo, "debs")
deb_files = (
    sorted(f for f in os.listdir(debs_dir) if f.lower().endswith(".deb"))
    if os.path.isdir(debs_dir)
    else []
)
indexed = set()
by_pkg = {}
for st in stanzas:
    filename = st.get("Filename", "")
    if filename:
        indexed.add(os.path.basename(filename))
    by_pkg.setdefault(st.get("Package", ""), []).append(st)


def stale_debs():
    return [f for f in deb_files if f not in indexed]


if mode == "list":
    if not stanzas:
        print("源里目前没有任何插件（Packages 为空）。用 manage.sh add <deb> 添加。")
    else:
        headers = ["Package", "名称", "版本", "架构", "大小", "deb 文件"]
        rows = []
        for pkg in sorted(by_pkg):
            for st in by_pkg[pkg]:
                rows.append(
                    [
                        pkg,
                        st.get("Name", ""),
                        st.get("Version", ""),
                        st.get("Architecture", ""),
                        human_size(st.get("Size", "")),
                        os.path.basename(st.get("Filename", "")),
                    ]
                )
        widths = [
            max([disp_width(h)] + [disp_width(r[i]) for r in rows])
            for i, h in enumerate(headers)
        ]
        print("源内插件：共 %d 个包 / %d 个索引条目" % (len(by_pkg), len(rows)))
        print("  " + "  ".join(pad(h, widths[i]) for i, h in enumerate(headers)))
        for row in rows:
            print("  " + "  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)))
    stale = stale_debs()
    if stale:
        print()
        print("未进索引的 deb（旧版本或重复文件，可用 manage.sh prune 清理）：")
        for name in stale:
            print("  debs/" + name)
    icon_dir = os.path.join(repo, "icons")
    if os.path.isdir(icon_dir):
        known = {pkg + ".png" for pkg in by_pkg}
        orphans = sorted(
            f for f in os.listdir(icon_dir) if f.lower().endswith(".png") and f not in known
        )
        if orphans:
            print()
            print("没有对应包条目的图标（可手动删除）：")
            for name in orphans:
                print("  icons/" + name)

elif mode == "info":
    group = by_pkg.get(arg)
    if not group:
        print("索引里没有包：%s" % arg, file=sys.stderr)
        sys.exit(2)
    print("%s —— %d 个架构条目" % (arg, len(group)))
    for st in group:
        print("-" * 60)
        for key, value in st.items():
            lines = str(value).split("\n")
            print("%s: %s" % (key, lines[0]))
            for extra in lines[1:]:
                print(" " + extra)

elif mode == "stale":
    for name in stale_debs():
        print("debs/" + name)

elif mode == "icon":
    icon_path = os.path.join(repo, "icons", arg + ".png")
    rel = os.path.relpath(icon_path, repo)
    if os.path.isfile(icon_path):
        size = os.path.getsize(icon_path)
        print("图标：%s（存在，%d 字节）" % (rel, size))
    else:
        print("图标：%s（不存在）" % rel)
        print(
            "提示：放一张 256×256 PNG 到 %s，"
            "并设置 BASE_URL 后重建，Sileo 里才会显示该图标。" % rel
        )
else:
    print("未知 inspect 模式：%s" % mode, file=sys.stderr)
    sys.exit(2)
PY
}

confirm_or_exit() {
  local prompt="$1"
  local answer
  if [ ! -t 0 ]; then
    die "非交互环境请加 -y 确认（会直接执行删除）。"
  fi
  read -r -p "$prompt [y/N] " answer || answer=""
  case "$answer" in
    y | Y | yes | YES | Yes) return 0 ;;
    *) echo "已取消。"; exit 0 ;;
  esac
}

do_remove() {
  local assume_yes=0
  local targets=()
  local item
  for item in "$@"; do
    case "$item" in
      -y | --yes) assume_yes=1 ;;
      *) targets+=("$item") ;;
    esac
  done
  [ "${#targets[@]}" -gt 0 ] || die "用法：manage.sh remove [-y] <Package 或 deb 文件名> [...]"

  shopt -s nullglob
  local files=()
  for item in "${targets[@]}"; do
    if [ -f "$REPO/debs/$item" ]; then
      files+=("$REPO/debs/$item")
      continue
    fi
    if [ -f "$item" ] && [[ "$item" == *.deb ]]; then
      files+=("$(cd "$(dirname "$item")" && pwd)/$(basename "$item")")
      continue
    fi
    local matches=("$REPO/debs/$item"_*.deb)
    if [ "${#matches[@]}" -gt 0 ]; then
      files+=("${matches[@]}")
    else
      die "找不到插件或 deb：$item（源里现有包见 manage.sh list）"
    fi
  done

  local uniq
  uniq="$(printf '%s\n' "${files[@]}" | sort -u)"
  mapfile -t files <<< "$uniq"

  echo "将删除以下 deb："
  local f
  for f in "${files[@]}"; do
    echo "  ${f##*/}"
  done
  if [ "$assume_yes" -ne 1 ]; then
    confirm_or_exit "确认删除这 ${#files[@]} 个 deb 并重建索引？"
  fi
  for f in "${files[@]}"; do
    rm -f "$f"
    echo "[删除] ${f##*/}"
  done
  echo
  "$PY" "$HERE/build.py" --verbose
  echo
  echo "提示：要同步清理不再被引用的图标，可删除 icons/<Package>.png（manage.sh list 会列出孤立图标）。"
}

do_prune() {
  local assume_yes=0
  if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then
    assume_yes=1
  fi
  local stale
  stale="$(inspect stale)"
  if [ -z "$stale" ]; then
    echo "没有未进索引的 deb，无需清理。"
    exit 0
  fi
  echo "以下 deb 未进索引（同包同架构只保留最高版本，其余可清理）："
  echo "$stale" | while IFS= read -r f; do echo "  $f"; done
  if [ "$assume_yes" -ne 1 ]; then
    confirm_or_exit "确认删除这 $(echo "$stale" | wc -l | tr -d ' ') 个旧 deb 并重建索引？"
  fi
  local f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    rm -f "$REPO/$f"
    echo "[清理] $f"
  done <<< "$stale"
  echo
  "$PY" "$HERE/build.py" --verbose
}

cmd="${1:-list}"
shift || true

case "$cmd" in
  list | ls)
    inspect list
    ;;
  info | show)
    [ "$#" -ge 1 ] || die "用法：manage.sh info <Package>"
    inspect info "$1"
    ;;
  add | publish)
    [ "$#" -ge 1 ] || die "用法：manage.sh add <deb> [deb...]"
    exec bash "$HERE/publish.sh" "$@"
    ;;
  remove | rm | del)
    do_remove "$@"
    ;;
  prune | clean)
    do_prune "$@"
    ;;
  icon)
    [ "$#" -ge 1 ] || die "用法：manage.sh icon <Package>"
    inspect icon "$1"
    ;;
  help | -h | --help)
    usage
    ;;
  *)
    die "未知子命令：$cmd（用 bash tools/manage.sh help 看用法）"
    ;;
esac
