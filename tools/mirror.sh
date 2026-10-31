#!/usr/bin/env bash
# ZYH 自用 越狱源 —— 同步 / 备份其他源的插件
#
# 用法：
#   bash tools/mirror.sh list <源地址> [--filter 关键词] [--limit 20] [--arch 架构]
#   bash tools/mirror.sh pull <源地址> 包名 [更多包名 ...] [--out 备份目录]
#   bash tools/mirror.sh pull <源地址> --all [--out 备份目录]              # 全源备份
#   bash tools/mirror.sh pull <源地址> 包名 --all-versions                  # 旧版本也备份（默认只备最新版）
#   bash tools/mirror.sh pull <源地址> 包名 --publish
#       └ 下载并校验后导入「ZYH 自用」源，自动重建索引 + 校验 + 生成部署包上传清单
#   bash tools/mirror.sh sync [--publish]
#       └ 按仓库根目录 sync-sources.txt 里配置的源，批量增量更新（新版本自动下载）
#
# 说明：
#   - 源地址可以是源根（如 https://repo.chariz.com）或 Packages 完整地址；
#     自动尝试 Packages / .xz / .bz2 / .gz。
#   - 下载后按该源索引里的 Size / MD5sum / SHA256 校验完整性。
#   - 默认只备份到「插件备份/<源域名>/」，不发布；加 --publish 才导入自己的源。
#   - sync 读取仓库根目录的 sync-sources.txt（每行一个源，可带包名或 --all）。
#   - 行尾加 --backup-only：这个源/插件只备份到本地，即使 sync --publish 也不进源。
#   - 只备份你有权备份的内容；第三方插件公开再分发可能侵权（尤其付费 / 授权插件）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
. "$HERE/_python.sh"

if [ "$#" -eq 0 ]; then
  sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi

COMMAND="$1"
PUBLISH=0
ARGS=("$@")
for arg in "$@"; do
  [ "$arg" = "--publish" ] && PUBLISH=1
done

SYNC_CONFIG="$REPO/sync-sources.txt"

if [ "$COMMAND" = "sync" ]; then
  [ -f "$SYNC_CONFIG" ] || {
    echo "[错误] 找不到配置文件：$SYNC_CONFIG" >&2
    echo "       参考格式（每行一个源）：" >&2
    echo "       https://repo.chariz.com 包名1 包名2" >&2
    echo "       https://havoc.app --all" >&2
    exit 2
  }
  echo
  echo "[同步] 读取配置：$SYNC_CONFIG"
  TOTAL=0
  OK=0
  FAIL=0
  FAILED_LINES=()
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    case "$line" in \#*) continue ;; esac
    TOTAL=$((TOTAL + 1))
    read -r -a fields <<< "$line"
    source_url="${fields[0]}"
    line_args=()
    backup_only=0
    for field in "${fields[@]:1}"; do
      if [ "$field" = "--backup-only" ]; then
        backup_only=1
      else
        line_args+=("$field")
      fi
    done
    if [ "$PUBLISH" -eq 1 ] && [ "$backup_only" -eq 0 ]; then
      line_args+=(--publish)
    fi
    if [ "${MIRROR_VERIFY_SSL:-0}" = "1" ]; then
      line_args+=(--verify-ssl)
    fi
    echo
    echo "────────────────────────────────────────────────────────────"
    if [ "$backup_only" -eq 1 ]; then
      echo "[同步·仅备份] $source_url ${line_args[*]}"
    else
      echo "[同步] $source_url ${line_args[*]}"
    fi
    if "$PY" "$HERE/mirror.py" pull "$source_url" "${line_args[@]}" --repo "$REPO"; then
      OK=$((OK + 1))
    else
      FAIL=$((FAIL + 1))
      FAILED_LINES+=("$line")
    fi
  done < "$SYNC_CONFIG"

  if [ "$TOTAL" -eq 0 ]; then
    echo "[提示] sync-sources.txt 里还没有配置任何源（只有注释/空行）。"
    exit 0
  fi

  echo
  echo "[同步汇总] 配置 $TOTAL 个源：成功 $OK 个 / 失败 $FAIL 个"
  if [ "$FAIL" -gt 0 ]; then
    echo "失败条目："
    printf '  %s\n' "${FAILED_LINES[@]}"
  fi

  if [ "$PUBLISH" -eq 1 ] && [ "$OK" -gt 0 ]; then
    echo
    echo "[重建] 重建本仓库索引 ……"
    "$PY" "$HERE/build.py" --verbose
    echo
    echo "[校验] 运行 tools/verify.sh ……"
    if ! bash "$HERE/verify.sh"; then
      echo "[错误] 校验未通过：可用 bash tools/manage.sh remove <包名> 移除有问题的包。" >&2
      exit 1
    fi
    echo
    echo "[部署包] 重建并对比上传清单 ……"
    bash "$HERE/phone-publish.sh" --diff
  fi

  [ "$FAIL" -eq 0 ] || exit 1
  exit 0
fi

if [ "$COMMAND" = "pull" ]; then
  ARGS+=(--repo "$REPO")
fi

echo
set +e
"$PY" "$HERE/mirror.py" "${ARGS[@]}"
RC=$?
set -e

if [ "$RC" -ne 0 ]; then
  echo
  echo "[提示] 下载有失败项（退出码 $RC）。失败的包不会进入源；可稍后重试。"
  exit "$RC"
fi

if [ "$COMMAND" = "pull" ] && [ "$PUBLISH" -eq 1 ]; then
  echo
  echo "[重建] 重建本仓库索引 ……"
  "$PY" "$HERE/build.py" --verbose

  echo
  echo "[校验] 运行 tools/verify.sh ……"
  if ! bash "$HERE/verify.sh"; then
    echo "[错误] 校验未通过：导入的 deb 可能字段不完整。可用 bash tools/manage.sh remove <包名> 移除后重新导入。" >&2
    exit 1
  fi

  echo
  echo "[部署包] 重建并对比上传清单 ……"
  bash "$HERE/phone-publish.sh" --diff
fi
