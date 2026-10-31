#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZYH 自用 越狱源 —— 构建器

扫描 debs/ 下的所有 .deb，重新生成 flat（扁平）apt 源所需的全部索引文件：

    Packages
    Packages.gz / Packages.bz2 / Packages.xz
    Release
    sileo-featured.json     （仅在 repo.conf 的 BASE_URL 非空时生成）

用法：
    ./tools/build.py                       # 重建索引
    ./tools/build.py --verbose             # 打印每个包的明细
    ./tools/build.py --set-base-url URL    # 写入托管地址并重建

只依赖 Python 标准库；有 dpkg-deb 时优先用它读取 control，否则内置 ar/tar 解析兜底。
"""

import argparse
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_PATH = os.path.join(ROOT, "repo.conf")
DEBS_DIR = os.path.join(ROOT, "debs")
PACKAGES_VARIANTS = ["Packages", "Packages.bz2", "Packages.gz", "Packages.xz"]

VERBOSE = False


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

class BuildError(Exception):
    """构建期可预期错误，打印中文提示后以非 0 退出。"""


def log(msg):
    print(msg)


def vlog(msg):
    if VERBOSE:
        print(msg)


def warn(msg):
    print("[警告] " + msg, file=sys.stderr)


def die(msg):
    print("[错误] " + msg, file=sys.stderr)
    sys.exit(1)


def write_bytes(path, data):
    """原子写文件（先写 .tmp 再替换），避免中断留下半个索引。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def hash_bytes(data, algo):
    h = hashlib.new(algo)
    h.update(data)
    return h.hexdigest()


def hash_file(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(256 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.2f MB" % (n / (1024.0 * 1024.0))


# --------------------------------------------------------------------------
# repo.conf：简易 shell 风格 KEY="value" 解析
# --------------------------------------------------------------------------

DEFAULT_CONF = {
    "REPO_ORIGIN": "ZYH 自用",
    "REPO_LABEL": "ZYH 自用",
    "REPO_SUITE": "stable",
    "REPO_VERSION": "1.0",
    "REPO_CODENAME": "zyh",
    "REPO_DESC": "ZYH 的个人自用越狱源",
    "REPO_DESC_LINE2": "",
    "BASE_URL": "",
    "FEATURED_TITLE": "ZYH 自用",
    "FEATURED_PACKAGE": "",
    "FEATURED_IMAGE": "assets/banner.png",
}

_CONF_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _parse_conf_value(raw):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "\"'":
        quote = raw[0]
        out = []
        i = 1
        escaped = False
        while i < len(raw):
            ch = raw[i]
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                break
            else:
                out.append(ch)
            i += 1
        return "".join(out)
    # 未加引号：允许行尾 ` # 注释`
    m = re.search(r"\s+#", raw)
    if m:
        raw = raw[: m.start()]
    return raw.strip()


def load_conf():
    conf = dict(DEFAULT_CONF)
    if not os.path.isfile(CONF_PATH):
        warn("未找到 repo.conf，使用内置默认配置")
        return conf
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        die("读取 repo.conf 失败：%s" % exc)
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _CONF_LINE_RE.match(stripped)
        if not m:
            warn("repo.conf 第 %d 行无法解析，已忽略：%s" % (lineno, stripped))
            continue
        conf[m.group(1)] = _parse_conf_value(m.group(2))
    return conf


def normalize_base_url(raw):
    url = (raw or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    url = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if url.lower().endswith("/index.html"):
        url = url[: -len("/index.html")].rstrip("/")
    if not re.match(r"^https?://[^/\s]+", url, re.I):
        die("BASE_URL 不合法：%s" % raw)
    return url


def update_conf_base_url(conf, url):
    """把 BASE_URL 写回 repo.conf，保留原有注释与其它行。"""
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    new_line = 'BASE_URL="%s"' % escaped
    if os.path.isfile(CONF_PATH):
        with open(CONF_PATH, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    else:
        lines = []
    replaced = False
    for idx, line in enumerate(lines):
        if re.match(r"^\s*BASE_URL\s*=", line):
            indent = re.match(r"^(\s*)", line).group(1)
            lines[idx] = indent + new_line
            replaced = True
            break
    if not replaced:
        while lines and lines[-1] == "":
            lines.pop()
        lines.append("")
        lines.append(new_line)
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    write_bytes(CONF_PATH, content.encode("utf-8"))


# --------------------------------------------------------------------------
# deb 的 control 读取：优先 dpkg-deb，退回内置 ar + tar 解析
# --------------------------------------------------------------------------

def read_deb_control(deb_path):
    dpkg = shutil.which("dpkg-deb")
    if dpkg:
        try:
            proc = subprocess.run(
                [dpkg, "-f", deb_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            proc = None
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.decode("utf-8")
        if proc is not None and proc.returncode != 0:
            warn(
                "dpkg-deb 无法读取 %s（%s），改用内置解析器兜底"
                % (os.path.basename(deb_path),
                   proc.stderr.decode("utf-8", "replace").strip())
            )
    return _read_deb_control_fallback(deb_path)


def _read_deb_control_fallback(deb_path):
    """纯 Python 读取 .deb（ar 归档）里的 control 文件。支持 control.tar.gz/xz/bz2。"""
    with open(deb_path, "rb") as fh:
        if fh.read(8) != b"!<arch>\n":
            raise BuildError("%s 不是标准 ar 归档（deb）" % os.path.basename(deb_path))
        long_names = b""
        while True:
            header = fh.read(60)
            if len(header) < 60:
                break
            if header[58:60] != b"`\n":
                raise BuildError("deb ar 头格式异常")
            name = header[0:16].decode("ascii", "replace").strip()
            try:
                size = int(header[48:58].decode("ascii", "replace").strip())
            except ValueError:
                raise BuildError("deb ar 成员大小异常")
            data = fh.read(size)
            if size % 2 == 1:
                fh.read(1)  # ar 对齐字节
            if name == "//":
                long_names = data
                continue
            if name.startswith("#1/"):
                name_len = int(name[3:])
                name = data[:name_len].decode("utf-8", "replace")
                data = data[name_len:]
            elif name.startswith("/") and name[1:].isdigit() and long_names:
                offset = int(name[1:])
                end = long_names.find(b"/\n", offset)
                if end < 0:
                    end = len(long_names)
                name = long_names[offset:end].decode("utf-8", "replace")
            name = name.rstrip("/")
            if name.startswith("control.tar"):
                return _extract_control_from_tar(data, name)
    raise BuildError("%s 中找不到 control.tar" % os.path.basename(deb_path))


def _extract_control_from_tar(data, member_name):
    name = member_name.lower()
    if name.endswith(".zst"):
        raise BuildError("control.tar.zst 需要 dpkg-deb 才能读取，请安装 dpkg 后重试")
    if name.endswith(".gz"):
        raw = gzip.decompress(data)
    elif name.endswith((".xz", ".lzma")):
        raw = lzma.decompress(data)
    elif name.endswith(".bz2"):
        raw = bz2.decompress(data)
    else:
        raw = data
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for member in tar.getmembers():
                if os.path.basename(member.name) == "control":
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        break
                    return extracted.read().decode("utf-8")
    except tarfile.TarError as exc:
        raise BuildError("解析 control.tar 失败：%s" % exc)
    raise BuildError("control.tar 里缺少 control 文件")


_FIELD_RE = re.compile(r"^([^\s:]+):[ \t]?(.*)$")


def parse_control(text):
    """把 control 文本解析成有序 [(字段, 值)]；值的多行续行原样保留（带前导空格）。"""
    fields = []
    current = None
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line == "":
            continue
        if raw_line[0] in " \t":
            if current is None:
                raise BuildError("control 出现无归属的续行：%r" % raw_line)
            current[1].append(raw_line)
            continue
        m = _FIELD_RE.match(raw_line)
        if not m:
            raise BuildError("无法解析 control 行：%r" % raw_line)
        current = [m.group(1), [m.group(2)]]
        fields.append(current)
    result = []
    seen = set()
    for key, lines in fields:
        if key in seen:
            raise BuildError("control 中字段重复：%s" % key)
        seen.add(key)
        result.append((key, "\n".join(lines)))
    return result


def format_field(key, value):
    lines = str(value).split("\n")
    out = "%s: %s" % (key, lines[0])
    for line in lines[1:]:
        out += "\n" + (line if line.startswith(" ") else " " + line)
    return out


# --------------------------------------------------------------------------
# dpkg 版本比较（用于同包同架构去重）
# --------------------------------------------------------------------------

def _order(ch):
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
            ac = _order(a[i]) if i < la else 0
            bc = _order(b[j]) if j < lb else 0
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
    """dpkg 的版本比较语义，返回 -1/0/1。"""
    a_epoch, _, a_rest = (a or "").partition(":")
    b_epoch, _, b_rest = (b or "").partition(":")
    if not a_rest:
        a_epoch, a_rest = "0", a_epoch
    if not b_rest:
        b_epoch, b_rest = "0", b_epoch
    try:
        a_epoch, b_epoch = int(a_epoch), int(b_epoch)
    except ValueError:
        a_epoch = b_epoch = 0
    if a_epoch != b_epoch:
        return 1 if a_epoch > b_epoch else -1
    a_up, _, a_rev = a_rest.rpartition("-")
    b_up, _, b_rev = b_rest.rpartition("-")
    if not a_up:
        a_up, a_rev = a_rest, ""
    if not b_up:
        b_up, b_rev = b_rest, ""
    diff = _verrevcmp(a_up, b_up)
    if diff:
        return 1 if diff > 0 else -1
    diff = _verrevcmp(a_rev, b_rev)
    if diff:
        return 1 if diff > 0 else -1
    return 0


# --------------------------------------------------------------------------
# 扫描 debs/ 与索引生成
# --------------------------------------------------------------------------

ARCH_ORDER = ["iphoneos-arm", "iphoneos-arm64", "iphoneos-arm64e"]

FIELD_ORDER = [
    "Package",
    "Name",
    "Version",
    "Architecture",
    "Section",
    "Priority",
    "Essential",
    "Maintainer",
    "Author",
    "Depends",
    "Pre-Depends",
    "Recommends",
    "Suggests",
    "Conflicts",
    "Breaks",
    "Replaces",
    "Provides",
    "Installed-Size",
    "Homepage",
    "Tag",
]

SPECIAL_SKIP = {
    "Icon",
    "Depiction",
    "SileoDepiction",
    "Header",
    "Filename",
    "Size",
    "MD5sum",
    "MD5Sum",
    "SHA1",
    "SHA256",
    "SHA512",
}

REQUIRED_CONTROL_FIELDS = ["Package", "Version", "Architecture", "Description", "Maintainer"]


def collect_debs(conf):
    if not os.path.isdir(DEBS_DIR):
        warn("debs/ 目录不存在，索引将为空")
        return []
    names = sorted(
        name
        for name in os.listdir(DEBS_DIR)
        if name.lower().endswith(".deb") and os.path.isfile(os.path.join(DEBS_DIR, name))
    )
    entries = []
    for name in names:
        path = os.path.join(DEBS_DIR, name)
        raw_control = read_deb_control(path)
        control = parse_control(raw_control)
        mapping = dict(control)
        missing = [field for field in REQUIRED_CONTROL_FIELDS if not mapping.get(field)]
        if missing:
            raise BuildError("%s 缺少 control 字段：%s" % (name, ", ".join(missing)))
        size = os.path.getsize(path)
        entry = {
            "file": name,
            "path": path,
            "filename": "debs/" + name,
            "control": control,
            "Package": mapping["Package"],
            "Version": mapping["Version"],
            "Architecture": mapping["Architecture"],
            "size": size,
            "md5": hash_file(path, "md5"),
            "sha256": hash_file(path, "sha256"),
        }
        entries.append(entry)
        vlog(
            "  扫描 %-52s %-16s %-14s %s"
            % (name, entry["Package"], entry["Version"], _human_size(size))
        )
    return entries


def dedupe_entries(entries):
    kept = {}
    dropped = []
    for entry in entries:
        key = (entry["Package"], entry["Architecture"])
        if key not in kept:
            kept[key] = entry
            continue
        current = kept[key]
        if compare_versions(entry["Version"], current["Version"]) > 0:
            dropped.append(current)
            kept[key] = entry
        else:
            dropped.append(entry)
    for entry in dropped:
        warn(
            "同包同架构存在旧版本，已从索引剔除：%s %s (%s)"
            % (entry["Package"], entry["Version"], entry["file"])
        )
    return list(kept.values()), dropped


def _arch_sort_key(arch):
    try:
        return (0, ARCH_ORDER.index(arch), arch)
    except ValueError:
        return (1, 0, arch)


def build_stanza(entry, conf):
    control = entry["control"]
    mapping = dict(control)
    pkg = entry["Package"]
    base_url = conf["BASE_URL"].rstrip("/")

    # --- Icon / Depiction 注入（只在有绝对托管地址时写，Sileo 不接受相对 URL） ---
    icon = None
    existing_icon = mapping.get("Icon", "").strip()
    icon_path = os.path.join(ROOT, "icons", pkg + ".png")
    if existing_icon and re.match(r"^https?://", existing_icon, re.I):
        icon = ("Icon", existing_icon)
    elif base_url and os.path.isfile(icon_path):
        icon = ("Icon", "%s/icons/%s.png" % (base_url, pkg))
    elif existing_icon:
        vlog("  跳过非绝对 Icon 字段：%s (%s)" % (pkg, existing_icon))

    depiction = None
    existing_depiction = mapping.get("Depiction", "").strip()
    if existing_depiction and re.match(r"^https?://", existing_depiction, re.I):
        depiction = ("Depiction", existing_depiction)
    elif base_url:
        depiction = ("Depiction", "%s/#%s" % (base_url, pkg))

    # --- 组装字段顺序：常用字段 → 其它原字段 → 注入字段 → 校验字段 → Description ---
    ordered = []
    used = set()

    def add(key, value):
        if key in used:
            return
        used.add(key)
        ordered.append((key, value))

    for key in FIELD_ORDER:
        if key in mapping and key != "Description":
            add(key, mapping[key])

    for key, value in control:
        if key in used or key in SPECIAL_SKIP or key == "Description":
            continue
        add(key, value)

    if icon:
        add(*icon)
    if depiction:
        add(*depiction)

    add("Filename", entry["filename"])
    add("Size", str(entry["size"]))
    add("MD5sum", entry["md5"])
    add("SHA256", entry["sha256"])

    # Description 必须是最后一个字段（它通常是多行）
    add("Description", mapping["Description"])
    return ordered


def render_packages(stanzas):
    if not stanzas:
        return ""
    chunks = ["\n".join(format_field(key, value) for key, value in stanza) for stanza in stanzas]
    return "\n\n".join(chunks) + "\n"


def compress_variants(packages_bytes):
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(packages_bytes)
    return {
        "Packages": packages_bytes,
        "Packages.bz2": bz2.compress(packages_bytes, compresslevel=9),
        "Packages.gz": gz_buf.getvalue(),
        "Packages.xz": lzma.compress(packages_bytes, format=lzma.FORMAT_XZ, preset=9),
    }


_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def release_date():
    t = time.gmtime()
    return "%s, %02d %s %04d %02d:%02d:%02d +0000" % (
        _DAY_NAMES[t.tm_wday],
        t.tm_mday,
        _MONTH_NAMES[t.tm_mon - 1],
        t.tm_year,
        t.tm_hour,
        t.tm_min,
        t.tm_sec,
    )


def render_release(conf, architectures, variants):
    lines = [
        "Origin: %s" % conf["REPO_ORIGIN"],
        "Label: %s" % conf["REPO_LABEL"],
        "Suite: %s" % conf["REPO_SUITE"],
        "Version: %s" % conf["REPO_VERSION"],
        "Codename: %s" % conf["REPO_CODENAME"],
        "Architectures: %s" % " ".join(architectures),
        "Components: main",
        "Description: %s" % conf["REPO_DESC"],
    ]
    if conf.get("REPO_DESC_LINE2"):
        lines.append(" " + conf["REPO_DESC_LINE2"])
    lines.append("Date: %s" % release_date())
    for algo, label in (("md5", "MD5Sum"), ("sha256", "SHA256")):
        lines.append("%s:" % label)
        for name in PACKAGES_VARIANTS:
            data = variants[name]
            lines.append(" %s %d %s" % (hash_bytes(data, algo), len(data), name))
    return "\n".join(lines) + "\n"


def update_featured(conf):
    """按模板生成 sileo-featured.json；BASE_URL 为空时清掉旧文件。"""
    target = os.path.join(ROOT, "sileo-featured.json")
    base_url = conf["BASE_URL"].rstrip("/")
    if not base_url or not (conf.get("FEATURED_PACKAGE") or "").strip():
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8") as fh:
                    old = fh.read()
            except OSError:
                old = ""
            if "FeaturedBannersView" in old:
                os.remove(target)
                log("[构建] 未启用精选横幅，已移除 sileo-featured.json")
        elif base_url:
            log("[构建] 未配置 FEATURED_PACKAGE，跳过精选横幅（sileo-featured.json）")
        return
    template = os.path.join(ROOT, "assets", "sileo-featured.template.json")
    if os.path.isfile(template):
        with open(template, "r", encoding="utf-8") as fh:
            raw = fh.read().replace("__BASE_URL__", base_url)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BuildError("assets/sileo-featured.template.json 不是合法 JSON：%s" % exc)
    else:
        warn("缺少 assets/sileo-featured.template.json，使用内置精选横幅内容")
        image = conf.get("FEATURED_IMAGE", "assets/banner.png").lstrip("/")
        data = {
            "class": "FeaturedBannersView",
            "itemSize": "{263, 148}",
            "itemCornerRadius": 10,
            "banners": [
                {
                    "title": conf.get("FEATURED_TITLE", conf["REPO_LABEL"]),
                    "package": conf.get("FEATURED_PACKAGE", ""),
                    "url": "%s/%s" % (base_url, image),
                    "displayText": False,
                }
            ],
        }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    write_bytes(target, payload.encode("utf-8"))
    log("[构建] sileo-featured.json 已生成")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def summary(conf, kept, entries, dropped, variants):
    archs = sorted({entry["Architecture"] for entry in kept}, key=_arch_sort_key)
    control_count = len(kept)
    log("")
    log("[构建完成] %d 个 deb / %d 个包条目 / 架构：%s"
        % (len(entries), control_count, " ".join(archs) if archs else "（空）"))
    for name in PACKAGES_VARIANTS:
        log("  %-14s %s" % (name, _human_size(len(variants[name]))))
    release_size = os.path.getsize(os.path.join(ROOT, "Release")) if os.path.isfile(os.path.join(ROOT, "Release")) else 0
    log("  %-14s %s" % ("Release", _human_size(release_size)))
    if dropped:
        log("  已剔除旧版本：%d 个（deb 文件仍保留在 debs/ 中）" % len(dropped))
    base_url = conf["BASE_URL"].rstrip("/")
    if base_url:
        log("")
        log("  源地址：%s/" % base_url)
        log("  Sileo 加源：sileo://source/%s/" % base_url)
    else:
        log("")
        log("  BASE_URL 未设置：暂不写包图标 / 精选横幅字段。")
        log('  设置托管地址：./tools/set-url.sh "https://<user>.github.io/<repo>"')


def main(argv):
    global VERBOSE
    parser = argparse.ArgumentParser(
        description="重建 ZYH 自用 越狱源的 apt 索引（Packages / Release / sileo-featured.json）"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="打印每个包的明细")
    parser.add_argument("--set-base-url", metavar="URL", help="写入 repo.conf 的 BASE_URL 并重建")
    args = parser.parse_args(argv)
    VERBOSE = args.verbose

    conf = load_conf()
    if args.set_base_url is not None:
        base_url = normalize_base_url(args.set_base_url)
        update_conf_base_url(conf, base_url)
        conf["BASE_URL"] = base_url
        log('[配置] BASE_URL 已写入 repo.conf：%s' % (base_url or "（空）"))
    else:
        conf["BASE_URL"] = normalize_base_url(conf.get("BASE_URL", ""))

    entries = collect_debs(conf)
    kept, dropped = dedupe_entries(entries)
    kept.sort(key=lambda e: (e["Package"], _arch_sort_key(e["Architecture"])))
    stanzas = [build_stanza(entry, conf) for entry in kept]

    packages_bytes = render_packages(stanzas).encode("utf-8")
    variants = compress_variants(packages_bytes)
    for name in PACKAGES_VARIANTS:
        write_bytes(os.path.join(ROOT, name), variants[name])
        vlog("  写入 %s" % name)

    archs = []
    for entry in kept:
        if entry["Architecture"] not in archs:
            archs.append(entry["Architecture"])
    archs.sort(key=_arch_sort_key)
    if not archs:
        # 空源：仍然对外声明标准 iOS 越狱架构，方便客户端识别本源
        archs = list(ARCH_ORDER)
    release = render_release(conf, archs, variants).encode("utf-8")
    write_bytes(os.path.join(ROOT, "Release"), release)

    update_featured(conf)
    summary(conf, kept, entries, dropped, variants)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BuildError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        print("\n[中断]", file=sys.stderr)
        sys.exit(130)
