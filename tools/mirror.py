#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步 / 备份其他越狱源的插件。

用法（一般通过 tools/mirror.sh 调用）：
    list  <源地址> [--filter 关键词] [--limit N]       列出该源的插件
    pull  <源地址> [包名 ...] [--all] [--out DIR]        下载并校验（默认只备份）
    pull  <源地址> [包名 ...] [--all] --publish          下载校验后导入「ZYH 自用」源

说明：
- 源地址可以是源根地址（自动找 Packages / Packages.xz / .bz2 / .gz），
  也可以直接给 Packages 索引完整地址。
- 下载后按该源 Packages 里的 Size / MD5sum / SHA256 校验，防截断 / 被篡改。
- --publish 会把 deb 拷进本仓库 debs/（文件名直接用插件包名 <Package>.deb；
  同包不同架构/版本并存时，其余文件自动改用 <Package>_<Version>_<Architecture>.deb），
  图标（若源里写了绝对 Icon 地址）拷进 icons/；
  之后由 mirror.sh 自动重建索引、校验并生成部署包上传清单。
- 只备份你有权备份的内容；把第三方源里的插件（尤其付费 / 授权插件）公开再分发可能侵权。
"""

import argparse
import hashlib
from collections import Counter
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata

UA = "Sileo/2.0 (iPhone; iOS 15.0; like Mac OS X) Sileo/2.0"
INDEX_CANDIDATES = ["Packages", "Packages.xz", "Packages.bz2", "Packages.gz"]


class MirrorError(Exception):
    pass


def die(msg):
    print("[错误] " + msg, file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print("[提示] " + msg, file=sys.stderr)


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------

_SSL_CTX = None


def ssl_context(verify_ssl):
    if verify_ssl:
        return ssl.create_default_context()
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl._create_unverified_context()
    return _SSL_CTX


class _RangeFallback(Exception):
    """服务端不支持 Range，退回普通下载。"""


CHUNK_SIZE = 128 * 1024
MAX_DOWNLOAD = 300 * 1024 * 1024


def _request(url, headers, verify_ssl, timeout):
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context(verify_ssl))


def _read_fully(response):
    chunks = []
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_range(url, verify_ssl, timeout):
    """用 HTTP Range 分块下载，绕过部分网络环境下大文件被截断的问题。"""
    data = bytearray()
    total = None
    while True:
        start = len(data)
        if total is not None and start >= total:
            break
        end = start + CHUNK_SIZE - 1
        headers = {"User-Agent": UA, "Range": "bytes=%d-%d" % (start, end)}
        last_error = None
        body = None
        stop = False
        for attempt in range(5):
            try:
                with _request(url, headers, verify_ssl, timeout) as response:
                    if response.status != 206:
                        raise _RangeFallback()
                    content_range = response.headers.get("Content-Range", "")
                    match = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range)
                    if match and match.group(3) != "*":
                        total = int(match.group(3))
                    body = _read_fully(response)
                break
            except _RangeFallback:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise
                if exc.code == 416:
                    stop = True
                    break
                last_error = exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            if attempt < 4:
                time.sleep(0.8 * (attempt + 1))
        if stop:
            break
        if body is None:
            raise MirrorError("分块下载失败：%s" % last_error)
        if not body:
            break
        data.extend(body)
        if len(data) > MAX_DOWNLOAD:
            raise MirrorError("文件超过 %d MB，已放弃" % (MAX_DOWNLOAD // 1024 // 1024))
        if total is None and len(body) < CHUNK_SIZE:
            break
    if total is not None and len(data) != total:
        raise MirrorError("下载不完整（%d / %d 字节）" % (len(data), total))
    return bytes(data)


def _fetch_plain(url, verify_ssl, timeout):
    last_error = None
    for attempt in range(3):
        try:
            with _request(url, {"User-Agent": UA}, verify_ssl, timeout) as response:
                return _read_fully(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise MirrorError("下载失败（%s）：%s" % (type(last_error).__name__, last_error))


def fetch(url, verify_ssl=False, timeout=120, retries=2):
    try:
        return _fetch_range(url, verify_ssl, timeout)
    except _RangeFallback:
        return _fetch_plain(url, verify_ssl, timeout)


def fetch_with_retry(url, verify_ssl):
    return fetch(url, verify_ssl=verify_ssl)


# --------------------------------------------------------------------------
# 索引解析 / 源地址
# --------------------------------------------------------------------------

def normalize_source(url):
    """返回 (index_url, root_url)。url 可以是源根或 Packages 地址。"""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    base = path.rsplit("/", 1)[-1]
    if base in INDEX_CANDIDATES:
        root = url[: len(url) - len(base)]
        return url, root
    root = url if url.endswith("/") else url + "/"
    return root + "Packages", root


def download_index(url, verify_ssl):
    """优先 Packages，失败再试压缩变体。返回 (index_url, 解压后的文本, root_url)。"""
    parsed = urllib.parse.urlparse(url)
    base = parsed.path.rsplit("/", 1)[-1]
    root = url[: len(url) - len(base)]
    candidates = []
    if base in INDEX_CANDIDATES:
        candidates.append((base, url))
        for name in INDEX_CANDIDATES:
            if name != base:
                candidates.append((name, root + name))
    else:
        for name in INDEX_CANDIDATES:
            candidates.append((name, root + name))

    last_error = None
    for name, candidate in candidates:
        try:
            raw = fetch_with_retry(candidate, verify_ssl)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                last_error = "%s 不存在" % candidate
                continue
            last_error = "%s HTTP %s" % (candidate, exc.code)
            continue
        except MirrorError as exc:
            last_error = str(exc)
            continue
        try:
            if name.endswith(".xz"):
                import lzma
                text = lzma.decompress(raw).decode("utf-8", "replace")
            elif name.endswith(".bz2"):
                import bz2
                text = bz2.decompress(raw).decode("utf-8", "replace")
            elif name.endswith(".gz"):
                import gzip
                text = gzip.decompress(raw).decode("utf-8", "replace")
            else:
                text = raw.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last_error = "%s 解压失败：%s" % (candidate, exc)
            continue
        return candidate, text, root
    raise MirrorError("找不到该源的 Packages 索引（%s）" % last_error)


def parse_packages(text):
    stanzas, cur, last = [], None, None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
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
        if cur is None:
            cur = {}
        cur[key.strip()] = [value.strip()]
        last = key.strip()
    if cur:
        stanzas.append(cur)
    return [{k: "\n".join(v) for k, v in s.items()} for s in stanzas]


# --------------------------------------------------------------------------
# 显示
# --------------------------------------------------------------------------

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


def package_of(stanza):
    return stanza.get("Package", "")


def _order_char(ch):
    if ch.isdigit():
        return 0
    if ch.isalpha():
        return ord(ch)
    if ch == "~":
        return -1
    if ch:
        return ord(ch) + 256
    return 0


def _verrevcmp(a, b):
    i = j = 0
    la, lb = len(a), len(b)
    while i < la or j < lb:
        first_diff = 0
        while (i < la and not a[i].isdigit()) or (j < lb and not b[j].isdigit()):
            ac = _order_char(a[i]) if i < la else 0
            bc = _order_char(b[j]) if j < lb else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < la and a[i] == "0":
            i += 1
        while j < lb and b[j] == "0":
            j += 1
        while i < la and a[i].isdigit() and j < lb and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < la and a[i].isdigit():
            return 1
        if j < lb and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def compare_versions(a, b):
    a = (a or "").partition(":")
    b = (b or "").partition(":")
    return _verrevcmp(a[2] or a[0], b[2] or b[0])


def select_stanzas(stanzas, names, arch_filter, text_filter):
    wanted = set(names)
    selected = []
    for stanza in stanzas:
        pkg = package_of(stanza)
        if not pkg:
            continue
        if wanted and pkg not in wanted:
            continue
        if arch_filter:
            archs = {a.strip() for a in arch_filter.split(",") if a.strip()}
            if stanza.get("Architecture") not in archs:
                continue
        if text_filter:
            haystack = (pkg + " " + stanza.get("Name", "")).lower()
            if text_filter.lower() not in haystack:
                continue
        selected.append(stanza)
    return selected


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------

def resolve_url(root, filename):
    filename = filename.strip()
    if re.match(r"^https?://", filename, re.I):
        return filename
    filename = filename.lstrip("./")
    return urllib.parse.urljoin(root, urllib.parse.quote(filename, safe="/"))


def safe_relpath(filename):
    filename = filename.strip().lstrip("./")
    parts = [p for p in filename.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise MirrorError("Filename 含非法路径：%r" % filename)
    return os.path.join(*parts) if parts else ""


def verify_bytes(data, stanza, label):
    problems = []
    size = stanza.get("Size")
    if size is not None:
        try:
            if len(data) != int(size):
                problems.append("大小不符（索引 %s，实际 %d）" % (size, len(data)))
        except ValueError:
            pass
    for field, algo in (("MD5sum", "md5"), ("SHA256", "sha256")):
        want = stanza.get(field)
        if not want:
            continue
        got = hashlib.new(algo, data).hexdigest()
        if got.lower() != want.strip().lower():
            problems.append("%s 不符（索引 %s，实际 %s）" % (field, want, got))
    if problems:
        raise MirrorError("%s 校验失败：%s" % (label, "；".join(problems)))


def download_deb(url, stanza, verify_ssl):
    data = fetch(url, verify_ssl=verify_ssl, timeout=180)
    verify_bytes(data, stanza, stanza.get("Package", "?"))
    return data


# --------------------------------------------------------------------------
# 命令：list
# --------------------------------------------------------------------------

def cmd_list(args):
    index_url, root = normalize_source(args.source)
    used_index, text, root = download_index(index_url, args.verify_ssl)
    stanzas = parse_packages(text)
    selected = select_stanzas(stanzas, [], getattr(args, "arch", ""), args.filter)
    print("源：%s" % args.source)
    print("索引：%s（共 %d 个 stanza）" % (used_index, len(stanzas)))
    if args.filter:
        print("筛选：%s → %d 条" % (args.filter, len(selected)))
    print()
    headers = ["Package", "名称", "版本", "架构", "大小"]
    rows = []
    for stanza in selected[: args.limit]:
        rows.append([
            package_of(stanza),
            stanza.get("Name", ""),
            stanza.get("Version", ""),
            stanza.get("Architecture", ""),
            human_size(stanza.get("Size", "")),
        ])
    if rows:
        widths = [max([disp_width(h)] + [disp_width(r[i]) for r in rows]) for i, h in enumerate(headers)]
        print("  " + "  ".join(pad(h, widths[i]) for i, h in enumerate(headers)))
        for row in rows:
            print("  " + "  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)))
    else:
        print("  （没有匹配的插件）")
    if len(selected) > args.limit:
        print()
        print("  …… 还有 %d 条未显示，用 --limit 或 --filter 查看" % (len(selected) - args.limit))
    print()
    print("用法提示：")
    print("  备份指定插件：bash tools/mirror.sh pull \"%s\" 包名1 包名2" % args.source)
    print("  备份全部插件：bash tools/mirror.sh pull \"%s\" --all" % args.source)
    print("  下载并导入自己的源：在上面命令上加 --publish")
    return 0


# --------------------------------------------------------------------------
# 命令：pull
# --------------------------------------------------------------------------

def host_dir(url):
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "source").replace(":", "_")
    return host


def cmd_pull(args):
    if not args.packages and not args.filter and not args.all:
        die("请指定要备份的包名，或加 --all 备份整源（例如：pull <源地址> 包名）")
    index_url, _ = normalize_source(args.source)
    used_index, text, root = download_index(index_url, args.verify_ssl)
    stanzas = parse_packages(text)
    selected = select_stanzas(stanzas, args.packages, args.arch, args.filter)
    if not selected:
        die("该源里没有匹配的插件（用 list 子命令查看包名）")
    # 命名规则：本次要同步的条目中，某包只有一条 → <包名>.deb（升级直接覆盖同名文件）；
    # 某包有多条（多架构/多版本）→ 全部用 <包名>_<版本>_<架构>.deb，避免互相覆盖。
    package_counts = Counter(stanza.get("Package") for stanza in selected)
    if not args.all_versions:
        newest = {}
        for stanza in selected:
            key = (stanza.get("Package", ""), stanza.get("Architecture", ""))
            if key not in newest or compare_versions(stanza.get("Version", ""), newest[key].get("Version", "")) > 0:
                newest[key] = stanza
        selected = list(newest.values())

    workspace = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.dirname(workspace)
    if args.publish:
        repo = os.path.abspath(args.repo or workspace)
        out_dir = None
    else:
        out_dir = os.path.abspath(args.out or os.path.join(workspace, "插件备份", host_dir(args.source)))
        os.makedirs(out_dir, exist_ok=True)

    print("源：%s" % args.source)
    print("索引：%s" % used_index)
    print("目标：%s" % ("导入自己的源 → " + repo + "/debs/" if args.publish else out_dir))
    print()

    downloaded = 0
    skipped = 0
    failed = 0
    previewed = 0
    results = []
    failed_urls = []
    for stanza in selected:
        pkg = package_of(stanza)
        version = stanza.get("Version", "")
        arch = stanza.get("Architecture", "")
        filename = stanza.get("Filename", "")
        label = "%s %s (%s)" % (pkg, version, arch)
        if not filename:
            warn("%s 没有 Filename 字段，跳过" % label)
            failed += 1
            continue
        url = resolve_url(root, filename)
        rel = safe_relpath(filename)
        if args.publish:
            # 同步进本源：文件名直接用插件包名（<Package>.deb）；多条目时用全名避免覆盖
            if package_counts.get(pkg, 0) > 1:
                target = os.path.join(repo, "debs", "%s_%s_%s.deb" % (pkg, version, arch))
            else:
                target = os.path.join(repo, "debs", pkg + ".deb")
        else:
            target = os.path.join(out_dir, rel)
        try:
            if os.path.isfile(target) and not args.force:
                data = open(target, "rb").read()
                try:
                    verify_bytes(data, stanza, label)
                    print("  [已存在] %-46s %s" % (label, human_size(len(data))))
                    skipped += 1
                    results.append({"package": pkg, "version": version, "architecture": arch,
                                    "filename": os.path.basename(target), "size": len(data), "status": "exists"})
                    continue
                except MirrorError:
                    if args.no_verify:
                        warn("%s 本地已存在但哈希与索引不符（--no-verify，保留使用）" % label)
                        print("  [已存在] %-46s %s" % (label, human_size(len(data))))
                        skipped += 1
                        results.append({"package": pkg, "version": version, "architecture": arch,
                                        "filename": os.path.basename(target), "size": len(data), "status": "exists"})
                        continue
                    warn("%s 同名文件内容与索引不符（可能是旧版本），重新下载覆盖" % label)
            if args.dry_run:
                print("  [预览] %-46s ← %s" % (label, url))
                previewed += 1
                continue
            print("  [下载] %-46s ← %s" % (label, url))
            data = None
            try:
                data = download_deb(url, stanza, args.verify_ssl)
            except MirrorError as exc:
                if "校验失败" not in str(exc):
                    raise
                # 哈希不符：换普通 GET 重试一次（部分 CDN 对 Range 请求返回不同内容）
                try:
                    data = _fetch_plain(url, args.verify_ssl, timeout=180)
                    verify_bytes(data, stanza, label)
                    warn("%s 分块下载哈希不符，改用普通 GET 重下后校验通过" % label)
                except Exception as exc2:  # noqa: BLE001
                    if args.no_verify:
                        try:
                            data = _fetch_plain(url, args.verify_ssl, timeout=180)
                        except Exception:  # noqa: BLE001
                            data = fetch(url, verify_ssl=args.verify_ssl, timeout=180)
                        warn("%s 哈希仍与索引不符，已按 --no-verify 跳过校验（%s）" % (label, exc2))
                    else:
                        raise MirrorError(
                            "%s：%s（若确认是该源索引哈希过期，可在 sync-sources.txt 对应行加 --no-verify 跳过校验）"
                            % (label, exc2)
                        )
            if data is None or data[:8] != b"!<arch>\n":
                raise MirrorError("%s 下载内容不是合法 deb（缺少 ar 头），已放弃" % label)
            os.makedirs(os.path.dirname(target) or target.rsplit(os.sep, 1)[0], exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
            try:
                os.chmod(target, 0o644)
            except OSError:
                pass
            print("  [完成] %-46s %s  校验通过" % (label, human_size(len(data))))
            downloaded += 1
            results.append({"package": pkg, "version": version, "architecture": arch,
                            "filename": os.path.basename(target), "size": len(data),
                            "md5": hashlib.md5(data).hexdigest(),
                            "sha256": hashlib.sha256(data).hexdigest(), "status": "downloaded"})
            # 图标（仅 --publish 且源里写了绝对 Icon 地址）
            if args.publish and not args.no_icons:
                icon_url = stanza.get("Icon", "")
                if re.match(r"^https?://", icon_url, re.I):
                    try:
                        icon_data = fetch(icon_url, verify_ssl=args.verify_ssl, timeout=60)
                        if icon_data[:8] == b"\x89PNG\r\n\x1a\n":
                            icons_dir = os.path.join(repo, "icons")
                            os.makedirs(icons_dir, exist_ok=True)
                            icon_path = os.path.join(icons_dir, pkg + ".png")
                            if not os.path.isfile(icon_path) or args.force:
                                with open(icon_path, "wb") as fh:
                                    fh.write(icon_data)
                                print("  [图标] %s" % os.path.join("icons", pkg + ".png"))
                    except Exception as exc:  # noqa: BLE001
                        warn("%s 图标下载失败：%s" % (pkg, exc))
        except MirrorError as exc:
            failed += 1
            failed_urls.append(url)
            print("  [失败] %s：%s" % (label, exc))

    # 备份清单
    if not args.publish:
        index_snapshot = os.path.join(out_dir, "Packages")
        with open(index_snapshot, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        manifest = {
            "source": args.source,
            "index_url": used_index,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "packages": results,
        }
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        lines = [
            "# 插件备份清单",
            "",
            "- 源：`%s`" % args.source,
            "- 索引：`%s`" % used_index,
            "- 时间：%s" % manifest["fetched_at"],
            "",
            "| Package | 版本 | 架构 | 文件 | 状态 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in results:
            lines.append("| %s | %s | %s | %s | %s |" % (
                item["package"], item["version"], item["architecture"],
                item["filename"], item["status"]))
        with open(os.path.join(out_dir, "manifest.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")

    print()
    if args.dry_run:
        print("完成（预览）：%d 个包，未实际下载" % previewed)
    else:
        print("完成：下载 %d 个 / 已存在 %d 个 / 失败 %d 个" % (downloaded, skipped, failed))
    if failed_urls:
        print("以下地址下载失败（可稍后重试）：")
        for failed_url in failed_urls:
            print("  " + failed_url)
    if args.publish:
        print("deb 已放入：%s" % os.path.join(repo, "debs"))
        print("接下来 mirror.sh 会自动重建索引、校验并生成部署包上传清单。")
    else:
        print("备份目录：%s" % out_dir)
        print("（清单：manifest.md / manifest.json；索引快照：Packages）")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mirror",
        description="同步 / 备份其他越狱源的插件（配合 tools/mirror.sh 使用）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出源里的插件")
    p_list.add_argument("source")
    p_list.add_argument("--filter", default="", help="按包名或显示名筛选（子串）")
    p_list.add_argument("--arch", default="", help="按架构筛选，如 iphoneos-arm64")
    p_list.add_argument("--limit", type=int, default=30, help="最多显示多少条，默认 30")
    p_list.add_argument("--verify-ssl", action="store_true", help="校验证书（默认跳过）")
    p_list.set_defaults(func=cmd_list)

    p_pull = sub.add_parser("pull", help="下载并校验（可选导入自己的源）")
    p_pull.add_argument("source")
    p_pull.add_argument("packages", nargs="*", help="要备份的 Package 名（可多个）")
    p_pull.add_argument("--all", action="store_true", help="备份该源全部插件")
    p_pull.add_argument("--all-versions", action="store_true", help="同包同架构的旧版本也一起备份（默认只备最新版）")
    p_pull.add_argument("--filter", default="", help="按包名/显示名子串筛选")
    p_pull.add_argument("--arch", default="", help="只备份指定架构，如 iphoneos-arm64")
    p_pull.add_argument("--out", default="", help="备份目录（默认 插件备份/<源域名>/）")
    p_pull.add_argument("--publish", action="store_true", help="下载后导入本仓库并重建/校验/生成部署包")
    p_pull.add_argument("--repo", default="", help=argparse.SUPPRESS)
    p_pull.add_argument("--no-icons", action="store_true", help="导入时不下载图标")
    p_pull.add_argument("--dry-run", action="store_true", help="只看会下载什么，不实际下载")
    p_pull.add_argument("--force", action="store_true", help="已存在也重新下载")
    p_pull.add_argument("--no-verify", action="store_true",
                        help="跳过 Size/MD5/SHA256 校验（仅当源索引哈希过期时用；仍会检查文件是合法 deb）")
    p_pull.add_argument("--verify-ssl", action="store_true", help="校验证书（默认跳过）")
    p_pull.set_defaults(func=cmd_pull)
    return parser


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.verify_ssl:
        warn("跳过 TLS 证书校验（本机 Python 没有根证书；只是个人备份用途）")
    try:
        return args.func(args)
    except MirrorError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        print("\n[中断]", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
