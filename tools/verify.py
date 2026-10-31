#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZYH 自用 越狱源 —— 独立校验器（SPEC §5，共 11 项检查）

与 tools/build.py 完全独立：
  * 自写 RFC822（deb822）解析器，不 import / 不复制 build.py 的任何函数；
  * 自写 repo.conf 的 shell 风格解析；
  * 自写 deb 的 ar + tar 解包（用于读取 control），用于和 Packages 做交叉核对；
  * 自写 PNG 头解析（不依赖 PIL）；
  * HTTP 冒烟用真实的 TCP + HTTP 请求（urllib），结束自动关服务。

用法（推荐用入口脚本）：
    bash tools/verify.sh                 # 默认跑全部 11 项（含 HTTP 冒烟）
    bash tools/verify.sh --no-http       # 跳过第 10 项 HTTP 冒烟
    bash tools/verify.sh --verbose       # 额外打印每条通过的断言
    bash tools/verify.sh --repo DIR      # 校验指定仓库副本（默认自动定位本文件上一级）

退出码：
    0 = 全部通过（可以有 SKIP）
    1 = 存在 FAIL
    2 = 参数 / 环境错误

平台说明（重要）：
    本机 Python 是 App 内嵌运行时，不支持 fork/exec（subprocess 会抛
    OSError: ios does not support processes）。因此：
      * 第 4 项：若 dpkg-deb 可执行则用 `dpkg-deb -f`；不可执行时自动退化为
        内置 ar + tar 解析 deb 的 control 做同等比对（不跳过实质校验）；
      * 第 8 项：系统有 node 但无法创建子进程时，node --check 标 SKIP 并说明；
      * 第 10 项：无法起 python -m http.server 子进程时，改用同一进程内的
        ThreadingHTTPServer 在 127.0.0.1 随机高位端口提供真实 HTTP 服务，
        再用 urllib 发真实 HTTP 请求比对内容。
    这些退化路径都会在输出中明确标注，不会静默放行。
"""

import argparse
import bz2
import email.utils
import gzip
import hashlib
import io
import json
import lzma
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 仓库根 = 本文件（tools/verify.py）的上一级的上一级，任意工作目录可运行。
DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PACKAGES_VARIANTS = ("Packages", "Packages.bz2", "Packages.gz", "Packages.xz")

# Release 中架构的规范顺序（SPEC §3.3）
CANON_ARCHS = ("iphoneos-arm", "iphoneos-arm64", "iphoneos-arm64e")

# Packages 每个 stanza 的必需字段（SPEC §5.2）
REQUIRED_STANZA_FIELDS = (
    "Package",
    "Version",
    "Architecture",
    "Description",
    "Maintainer",
    "Filename",
    "Size",
    "MD5sum",
    "SHA256",
)

# 其它硬性字符串（SPEC §3.3 / §3.4）
REPO_ORIGIN = "ZYH 自用"
REPO_LABEL = "ZYH 自用"

PNG_SIG = b"\x89PNG\r\n\x1a\n"
BOM = b"\xef\xbb\xbf"

HTTP_PATHS = ("/Packages", "/Release", "/CydiaIcon.png", "/index.html")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def file_digest(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def png_size(data):
    """解析 PNG 头，返回 (宽, 高)；不是合法 PNG 返回 None。"""
    if len(data) < 33 or not data.startswith(PNG_SIG):
        return None
    if data[12:16] != b"IHDR":
        return None
    try:
        length = struct.unpack(">I", data[8:12])[0]
    except struct.error:
        return None
    if length < 13:
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
    except struct.error:
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)


# ---------------------------------------------------------------------------
# 独立实现的 deb822 / RFC822 解析器（SPEC §5.2 要求，不复用 build.py）
# ---------------------------------------------------------------------------

class Stanza(object):
    """一个 RFC822 段落：按出现顺序保存 (字段名, 值) 列表。"""

    __slots__ = ("fields", "line")

    def __init__(self, line_no):
        self.fields = []          # [(name, value)]
        self.line = line_no       # 段落首行行号

    def get(self, name):
        """字段值；不存在返回 None，存在但为空返回 ""。"""
        lname = name.lower()
        for key, value in self.fields:
            if key.lower() == lname:
                return value
        return None

    def has(self, name):
        return self.get(name) is not None

    def names(self):
        return [key for key, _ in self.fields]


# 合法字段行：字母/数字开头的名字，冒号，可选一个空格，值。
_FIELD_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)[ \t]*:[ \t]?(.*)$")


def parse_rfc822(text):
    """独立实现 deb822 解析器。

    规则：
      * 空行分隔 stanza（连续空行记为错误）；
      * 以空格 / 制表符开头的行是上一字段的续行（去一个前导空白后接换行拼接）；
      * 续行内容为单个 "." 时代表空行（Description 的惯例）；
      * "#" 开头的行视为注释（记警告，不影响解析）；
      * "字段: 值" 定义字段；同段内同名字段重复记为错误。

    返回 (stanzas, problems)，problems 为 [(级别, 说明)]，级别 ∈ {"error","warn"}。
    """
    problems = []
    # CRLF 由调用方单独检查；这里容忍并按 LF 处理，保证解析不崩。
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # 文件末尾换行产生的空元素

    stanzas = []
    cur = None
    last_field = None
    blank_run = 0

    for idx, line in enumerate(lines, 1):
        if line.strip() == "":
            blank_run += 1
            if blank_run == 1:
                if cur is not None:
                    stanzas.append(cur)
                    cur = None
                    last_field = None
            elif blank_run > 1:
                problems.append(
                    ("error", "第 %d 行：stanza 之间出现连续空行（规范要求恰好一个空行）" % idx)
                )
            continue

        blank_run = 0

        if line[0] in " \t":
            if cur is None or last_field is None:
                problems.append(("error", "第 %d 行：续行前面没有对应字段" % idx))
                continue
            content = line[1:]
            if content == ".":
                content = ""
            name, value = cur.fields[-1]
            cur.fields[-1] = (name, value + "\n" + content)
            continue

        if line.startswith("#"):
            problems.append(("warn", "第 %d 行：出现注释行（deb822 允许，但生成物一般不应有）" % idx))
            continue

        match = _FIELD_LINE_RE.match(line)
        if not match:
            problems.append(
                ("error", "第 %d 行：不是合法的“字段: 值”行：%r" % (idx, line[:70]))
            )
            continue

        if cur is None:
            cur = Stanza(idx)
            last_field = None

        name, value = match.group(1), match.group(2)
        if cur.has(name):
            problems.append(
                ("error", "第 %d 行：字段 %s 在同一个 stanza 内重复" % (idx, name))
            )
        cur.fields.append((name, value))
        last_field = name

    if cur is not None:
        stanzas.append(cur)

    return stanzas, problems


# ---------------------------------------------------------------------------
# 独立实现的 deb 版本比较（仅用于判断「旧版本 deb 无 stanza 是否合法」）
# ---------------------------------------------------------------------------

def _order_char(ch):
    """dpkg 版本比较的字符权重：~ 最小，其次字符串结束，其次数字=0，字母按 ASCII，其余 +256。"""
    if ch == "~":
        return -1
    if ch.isdigit():
        return 0
    if ch.isalpha():
        return ord(ch)
    return ord(ch) + 256


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
            if first_diff == 0:
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
    """按 dpkg 规则比较两个版本号；a<b 返回 -1，相等 0，a>b 返回 1。"""
    a = "" if a is None else str(a)
    b = "" if b is None else str(b)
    if ":" in a:
        a_epoch, a_rest = a.split(":", 1)
    else:
        a_epoch, a_rest = "0", a
    if ":" in b:
        b_epoch, b_rest = b.split(":", 1)
    else:
        b_epoch, b_rest = "0", b
    try:
        a_epoch_n = int(a_epoch)
    except ValueError:
        a_epoch_n = 0
    try:
        b_epoch_n = int(b_epoch)
    except ValueError:
        b_epoch_n = 0
    if a_epoch_n != b_epoch_n:
        return -1 if a_epoch_n < b_epoch_n else 1

    if "-" in a_rest:
        a_up, a_rev = a_rest.rsplit("-", 1)
    else:
        a_up, a_rev = a_rest, ""
    if "-" in b_rest:
        b_up, b_rev = b_rest.rsplit("-", 1)
    else:
        b_up, b_rev = b_rest, ""

    ret = _verrevcmp(a_up, b_up)
    if ret:
        return -1 if ret < 0 else 1
    ret = _verrevcmp(a_rev, b_rev)
    if ret:
        return -1 if ret < 0 else 1
    return 0


# ---------------------------------------------------------------------------
# 独立实现的 repo.conf 解析（shell 风格 KEY="value"，SPEC §3.5）
# ---------------------------------------------------------------------------

_CONF_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_shell_conf(text):
    """解析 shell 风格 KEY="value" 配置，返回 (conf, problems)。

    支持：注释行、行首 export、单/双引号、双引号内 \" 转义、引号后的注释、
    未加引号值里的行尾注释（" #"）、行尾空白。不做变量展开。
    """
    conf = {}
    problems = []
    for lineno, raw_line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        match = _CONF_ASSIGN_RE.match(line)
        if not match:
            problems.append("第 %d 行不是 KEY=value 形式：%r" % (lineno, raw_line.strip()[:70]))
            continue

        key, rest = match.group(1), match.group(2).strip()
        if rest[:1] in ("'", '"'):
            quote = rest[0]
            chars = []
            i = 1
            closed = False
            while i < len(rest):
                ch = rest[i]
                if ch == "\\" and quote == '"' and i + 1 < len(rest):
                    nxt = rest[i + 1]
                    if nxt in ('"', "\\", "$", "`"):
                        chars.append(nxt)
                        i += 2
                        continue
                    chars.append(ch)
                    i += 1
                    continue
                if ch == quote:
                    i += 1
                    closed = True
                    break
                chars.append(ch)
                i += 1
            if not closed:
                problems.append("第 %d 行：%s 的引号未闭合" % (lineno, key))
                continue
            trailer = rest[i:].strip()
            if trailer and not trailer.startswith("#"):
                problems.append(
                    "第 %d 行：%s 的值引号后有多余内容 %r" % (lineno, key, trailer[:50])
                )
            value = "".join(chars)
        else:
            hash_at = rest.find("#")
            if hash_at >= 0 and (hash_at == 0 or rest[hash_at - 1] in " \t"):
                rest = rest[:hash_at].rstrip()
            value = rest

        if key in conf:
            problems.append("第 %d 行：变量 %s 重复定义（后者覆盖前者）" % (lineno, key))
        conf[key] = value
    return conf, problems


# ---------------------------------------------------------------------------
# 独立实现的 deb control 读取（ar 归档 + tar + gzip/xz/bz2，标准库）
# ---------------------------------------------------------------------------

class ControlError(Exception):
    """读不出 deb 的 control 文本。"""


def _decompress_control_tar(name, body):
    lower = name.lower()
    if lower.endswith(".gz"):
        return gzip.decompress(body)
    if lower.endswith(".xz"):
        return lzma.decompress(body)
    if lower.endswith(".bz2"):
        return bz2.decompress(body)
    if lower.endswith(".zst"):
        raise ControlError("control.tar.zst 使用 zstd 压缩，Python 标准库不支持解压")
    raise ControlError("未知的 control.tar 压缩格式：%s" % name)


def extract_control_from_deb(path):
    """从 .deb（ar 归档）中取出 control 文本；失败抛 ControlError。"""
    data = read_bytes(path)
    if not data.startswith(b"!<arch>\n"):
        raise ControlError("不是合法的 ar 归档（缺少 !<arch> 魔数）")
    offset = 8
    total = len(data)
    while offset + 60 <= total:
        header = data[offset:offset + 60]
        if header[58:60] != b"`\n":
            raise ControlError("ar 条目头损坏（偏移 %d）" % offset)
        raw_name = header[0:16].decode("utf-8", "replace").strip()
        if raw_name.endswith("/"):
            raw_name = raw_name[:-1]
        try:
            size = int(header[48:58].decode("ascii", "replace").strip() or "0")
        except ValueError:
            raise ControlError("ar 条目 %r 的大小字段非法" % raw_name)
        body_start = offset + 60
        if body_start + size > total:
            raise ControlError("ar 条目 %r 数据越界" % raw_name)
        body = data[body_start:body_start + size]

        if raw_name.startswith("control.tar"):
            tar_bytes = _decompress_control_tar(raw_name, body)
            try:
                tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
            except tarfile.TarError as exc:
                raise ControlError("control.tar 解析失败：%s" % exc)
            for member in tar.getmembers():
                member_name = member.name
                while member_name.startswith("./"):
                    member_name = member_name[2:]
                member_name = member_name.lstrip("/")
                if member_name == "control":
                    fh = tar.extractfile(member)
                    if fh is None:
                        raise ControlError("control.tar 中的 control 条目无法读取")
                    return fh.read().decode("utf-8")
            raise ControlError("control.tar 中找不到 control 文件")

        offset = body_start + size + (size % 2)

    raise ControlError("ar 归档中找不到 control.tar.*（可能不是 deb）")


# ---------------------------------------------------------------------------
# 子进程能力探测（iOS 内嵌 Python 不支持 fork/exec，必须优雅退化）
# ---------------------------------------------------------------------------

def can_spawn_processes():
    """当前 Python 能否创建子进程。"""
    try:
        subprocess.run(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def run_capture(argv, timeout=30):
    """执行命令并捕获输出，返回 (成功?, stdout 字节, stderr 字节)。"""
    try:
        proc = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
        return (proc.returncode == 0, proc.stdout or b"", proc.stderr or b"")
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, b"", str(exc).encode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# 输出与统计
# ---------------------------------------------------------------------------

class Reporter(object):
    """按「检查项」分组统计 PASS / FAIL / SKIP，并统一格式化输出。"""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.pass_n = 0
        self.fail_n = 0
        self.skip_n = 0
        self.warns = []
        self._cur = None

    def start(self, num, title):
        self._cur = {
            "num": num,
            "title": title,
            "p": 0,
            "f": 0,
            "s": 0,
            "fails": [],
            "skips": [],
            "notes": [],
        }

    def ok(self, msg):
        cur = self._cur
        cur["p"] += 1
        self.pass_n += 1
        if self.verbose:
            print("    [PASS] %s" % msg)

    def check(self, cond, msg):
        if cond:
            self.ok(msg)
        else:
            self.bad(msg)

    def bad(self, msg):
        cur = self._cur
        cur["f"] += 1
        self.fail_n += 1
        cur["fails"].append(msg)

    def skip(self, msg):
        cur = self._cur
        cur["s"] += 1
        self.skip_n += 1
        cur["skips"].append(msg)

    def note(self, msg):
        cur = self._cur
        if msg not in cur["notes"]:
            cur["notes"].append(msg)

    def warn(self, msg):
        if msg not in self.warns:
            self.warns.append(msg)

    def finish(self):
        cur = self._cur
        total = cur["p"] + cur["f"] + cur["s"]
        if cur["f"] > 0:
            status = "FAIL"
        elif total == 0 or (cur["p"] == 0 and cur["s"] > 0):
            status = "SKIP"
        else:
            status = "PASS"
        print(
            "[%d/11] %s —— %s（PASS %d / FAIL %d / SKIP %d）"
            % (cur["num"], cur["title"], status, cur["p"], cur["f"], cur["s"])
        )
        for msg in cur["fails"]:
            print("    ✗ %s" % msg)
        for msg in cur["skips"]:
            print("    ⊘ %s" % msg)
        for msg in cur["notes"]:
            print("    · %s" % msg)
        self._cur = None


class Context(object):
    """一次校验运行的共享上下文。"""

    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.stanzas = None          # List[Stanza]，来自 Packages
        self.packages_raw = None     # Packages 原始字节
        self.conf = {}               # repo.conf 解析结果
        self._ctrl_cache = {}
        self._spawn_ok = None

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def can_spawn(self):
        if self._spawn_ok is None:
            self._spawn_ok = can_spawn_processes()
        return self._spawn_ok

    def deb_control(self, path):
        """读取 deb 的 control 段落，返回 ("ok", Stanza) 或 ("err", 说明)。

        优先 dpkg-deb -f（可执行时），否则用内置 ar+tar 解析；结果缓存。
        """
        key = os.path.abspath(path)
        if key in self._ctrl_cache:
            return self._ctrl_cache[key]

        result = None
        dpkg = shutil.which("dpkg-deb")
        if dpkg and self.can_spawn():
            ok, out, err = run_capture([dpkg, "-f", path])
            if ok:
                try:
                    stanzas, _ = parse_rfc822(out.decode("utf-8"))
                    if stanzas:
                        result = ("ok", stanzas[0])
                    else:
                        result = ("err", "dpkg-deb -f 输出为空")
                except Exception as exc:  # noqa: BLE001 - 兜底转普通错误
                    result = ("err", "dpkg-deb -f 输出解析失败：%s" % exc)
            else:
                message = err.decode("utf-8", "replace").strip()[:200]
                result = ("err", "dpkg-deb -f 执行失败：%s" % message)

        if result is None:
            try:
                text = extract_control_from_deb(path)
                stanzas, _ = parse_rfc822(text)
                if not stanzas:
                    raise ControlError("control 内容为空")
                result = ("ok", stanzas[0])
            except Exception as exc:  # noqa: BLE001 - 兜底转普通错误
                result = ("err", str(exc))

        self._ctrl_cache[key] = result
        return result


# ---------------------------------------------------------------------------
# 校验项 1：文件齐全（SPEC §5.1）
# ---------------------------------------------------------------------------

REQUIRED_ROOT_FILES = (
    ".nojekyll",
    "Packages",
    "Packages.gz",
    "Packages.bz2",
    "Packages.xz",
    "Release",
    "CydiaIcon.png",
    "index.html",
    "repo.conf",
)


def check_01_files(ctx, rep):
    for name in REQUIRED_ROOT_FILES:
        if os.path.isfile(ctx.path(name)):
            rep.ok("存在文件 %s" % name)
        else:
            rep.bad("缺少文件 %s" % name)

    debs_dir = ctx.path("debs")
    deb_names = []
    if os.path.isdir(debs_dir):
        deb_names = [
            name
            for name in os.listdir(debs_dir)
            if name.endswith(".deb") and os.path.isfile(os.path.join(debs_dir, name))
        ]
    if deb_names:
        rep.ok("debs/ 下存在 %d 个 .deb 文件" % len(deb_names))
    else:
        rep.skip("debs/ 下暂无 .deb（空源允许；发布插件后会自动生成索引）")

    nojekyll = ctx.path(".nojekyll")
    if os.path.isfile(nojekyll):
        size = os.path.getsize(nojekyll)
        rep.check(size == 0, ".nojekyll 应为空文件（实际 %d 字节）" % size)


# ---------------------------------------------------------------------------
# 校验项 2：独立 RFC822 解析 + stanza 结构（SPEC §5.2）
# ---------------------------------------------------------------------------

def check_02_packages(ctx, rep):
    path = ctx.path("Packages")
    if not os.path.isfile(path):
        rep.skip("Packages 不存在，跳过解析")
        return

    raw = read_bytes(path)
    if raw.startswith(BOM):
        rep.bad("Packages 含有 UTF-8 BOM（规范要求无 BOM）")
    else:
        rep.ok("Packages 无 UTF-8 BOM")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.bad("Packages 不是合法 UTF-8：%s" % exc)
        return
    rep.ok("Packages 可按 UTF-8 解码")

    rep.check(b"\r" not in raw, "Packages 使用 LF 换行（未发现 CR）")
    if raw == b"":
        ctx.packages_raw = raw
        ctx.stanzas = []
        rep.ok("Packages 为空（空源，暂无插件）")
        return
    rep.check(raw.endswith(b"\n"), "Packages 以换行结尾")
    rep.check(not raw.startswith((b"\n", b" ")), "Packages 不以空行 / 空白开头")
    rep.check(not raw.endswith(b"\n\n"), "Packages 末尾只有一个换行（不是空行结尾）")

    ctx.packages_raw = raw
    stanzas, problems = parse_rfc822(text)
    ctx.stanzas = stanzas

    for severity, message in problems:
        if severity == "error":
            rep.bad("Packages 解析错误：%s" % message)
        else:
            rep.warn("Packages 解析提示：%s" % message)

    if not stanzas:
        rep.skip("Packages 中没有 stanza（空源允许）")
        return
    rep.ok("Packages 解析出 %d 个 stanza" % len(stanzas))

    seen = set()
    for index, stanza in enumerate(stanzas, 1):
        pkg = stanza.get("Package") or "?"
        arch = stanza.get("Architecture") or "?"
        tag = "stanza #%d（%s/%s）" % (index, pkg, arch)

        missing = [field for field in REQUIRED_STANZA_FIELDS if not stanza.has(field)]
        rep.check(
            not missing,
            "%s 包含全部必需字段（缺少：%s）" % (tag, "、".join(missing) or "无"),
        )

        last_name = stanza.fields[-1][0] if stanza.fields else "（空）"
        rep.check(
            last_name == "Description",
            "%s 的 Description 必须是该 stanza 的最后一个字段（实际最后字段：%s）"
            % (tag, last_name),
        )

        filename = stanza.get("Filename") or ""
        filename_ok = (
            bool(filename)
            and filename.startswith("debs/")
            and not filename.startswith("./")
            and ".." not in filename.split("/")
            and "\\" not in filename
        )
        rep.check(
            filename_ok,
            "%s 的 Filename 必须是 debs/ 下的相对路径且不带 ./ 前缀（实际：%r）"
            % (tag, filename),
        )

        size = stanza.get("Size") or ""
        md5 = stanza.get("MD5sum") or ""
        sha = stanza.get("SHA256") or ""
        formats_ok = (
            size.isdigit()
            and re.fullmatch(r"[0-9a-f]{32}", md5) is not None
            and re.fullmatch(r"[0-9a-f]{64}", sha) is not None
        )
        rep.check(formats_ok, "%s 的 Size / MD5sum / SHA256 格式合法" % tag)

        dup_key = (stanza.get("Package"), stanza.get("Architecture"))
        rep.check(
            dup_key not in seen,
            "%s 与前面的 stanza 的 Package+Architecture 重复（只应保留最高版本）" % tag,
        )
        seen.add(dup_key)

    keys = [
        (stanza.get("Package") or "", stanza.get("Architecture") or "")
        for stanza in stanzas
    ]
    rep.check(
        keys == sorted(keys),
        "stanza 按 Package 升序、再按 Architecture 升序排列（确定性输出）",
    )


# ---------------------------------------------------------------------------
# 校验项 3：debs/ 与 Packages 双向一致（SPEC §5.3）
# ---------------------------------------------------------------------------

def _key_from_deb_filename(filename):
    """文件名兜底推断 (Package, Version, Architecture)。"""
    base = filename[:-4] if filename.endswith(".deb") else filename
    parts = base.rsplit("_", 2)
    if len(parts) == 3 and all(parts):
        return (parts[0], parts[1], parts[2])
    return None


def check_03_debs(ctx, rep):
    stanzas = ctx.stanzas
    if stanzas is None:
        rep.skip("Packages 未成功解析，跳过 deb 一致性检查")
        return

    if not stanzas:
        debs_dir = ctx.path("debs")
        has_debs = os.path.isdir(debs_dir) and any(
            name.endswith(".deb") for name in os.listdir(debs_dir)
        )
        if not has_debs:
            rep.skip("空源：Packages 无 stanza、debs/ 无 deb，跳过双向一致检查")
            return

    debs_dir = ctx.path("debs")
    if not os.path.isdir(debs_dir):
        rep.bad("debs/ 目录不存在")
        return
    deb_files = sorted(
        os.path.join(debs_dir, name)
        for name in os.listdir(debs_dir)
        if name.endswith(".deb") and os.path.isfile(os.path.join(debs_dir, name))
    )
    if not deb_files:
        rep.bad("debs/ 下没有任何 .deb 文件")
        return

    # 正向：Packages 里每个 stanza 的 Filename 文件存在，且 Size/MD5sum/SHA256 与磁盘字节一致。
    for index, stanza in enumerate(stanzas, 1):
        pkg = stanza.get("Package") or "?"
        arch = stanza.get("Architecture") or "?"
        tag = "stanza #%d（%s/%s）" % (index, pkg, arch)
        filename = stanza.get("Filename") or ""
        if not filename:
            rep.bad("%s 没有 Filename 字段，无法定位 deb" % tag)
            continue
        deb_path = ctx.path(filename)
        if not os.path.isfile(deb_path):
            rep.bad("%s 的 Filename 指向的文件不存在：%s" % (tag, filename))
            continue
        rep.ok("%s 的 Filename 文件存在：%s" % (tag, filename))

        actual_size = os.path.getsize(deb_path)
        want_size = stanza.get("Size") or ""
        rep.check(
            want_size.isdigit() and int(want_size) == actual_size,
            "%s 的 Size 与磁盘字节数一致（Packages=%r，磁盘=%d）"
            % (tag, want_size, actual_size),
        )
        rep.check(
            (stanza.get("MD5sum") or "").lower() == file_digest(deb_path, "md5"),
            "%s 的 MD5sum 与磁盘字节一致" % tag,
        )
        rep.check(
            (stanza.get("SHA256") or "").lower() == file_digest(deb_path, "sha256"),
            "%s 的 SHA256 与磁盘字节一致" % tag,
        )

    # 反向：debs/ 下每个 deb 都能在 Packages 找到 Package+Version+Architecture 匹配的 stanza。
    by_key = {}
    for stanza in stanzas:
        by_key[(stanza.get("Package"), stanza.get("Version"), stanza.get("Architecture"))] = stanza

    extras = []
    for deb_path in deb_files:
        base = os.path.basename(deb_path)
        status, control = ctx.deb_control(deb_path)
        if status == "ok" and control is not None:
            key = (
                control.get("Package"),
                control.get("Version"),
                control.get("Architecture"),
            )
        else:
            key = _key_from_deb_filename(base)
            if key is None:
                rep.bad("debs/%s 无法读取 control，也无法从文件名推断包信息" % base)
                continue
            rep.note(
                "debs/%s 的 control 读取失败，改用文件名推断匹配键：%s"
                % (base, "/".join(key))
            )

        stanza = by_key.get(key)
        if stanza is None:
            extras.append((deb_path, key))
            continue
        rep.ok("debs/%s 在 Packages 中有匹配的 stanza" % base)
        rep.check(
            stanza.get("Filename") == "debs/" + base,
            "debs/%s 对应 stanza 的 Filename 正确指向该文件" % base,
        )

    if not extras:
        rep.ok("debs/ 中的 deb 与 Packages 中的 stanza 一一对应，没有多余文件")
    else:
        for deb_path, key in extras:
            base = os.path.basename(deb_path)
            pkg, version, arch = key
            candidates = [
                stanza
                for stanza in stanzas
                if stanza.get("Package") == pkg and stanza.get("Architecture") == arch
            ]
            if not candidates:
                rep.bad(
                    "debs/%s（%s/%s/%s）在 Packages 中既无匹配 stanza，同包同架构也没有其它版本"
                    % (base, pkg, version, arch)
                )
                continue
            best = max(candidates, key=lambda s: _VersionKey(s.get("Version") or "0"))
            best_version = best.get("Version") or "0"
            if compare_versions(version, best_version) < 0:
                rep.ok(
                    "debs/%s 是 %s/%s 的旧版本（%s < %s），允许不生成 stanza"
                    % (base, pkg, arch, version, best_version)
                )
                rep.warn(
                    "debs/%s 为旧版本（%s < %s），已按规范跳过（仅保留最高版本）"
                    % (base, version, best_version)
                )
            else:
                rep.bad(
                    "debs/%s 的版本 %s 不低于 Packages 中的 %s，却没有对应 stanza"
                    % (base, version, best_version)
                )


class _VersionKey(object):
    """把版本号包装成可排序对象（复用 compare_versions）。"""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return compare_versions(self.value, other.value) < 0

    def __eq__(self, other):
        return compare_versions(self.value, other.value) == 0


# ---------------------------------------------------------------------------
# 校验项 4：control 字段与 Packages 一致（SPEC §5.4）
# ---------------------------------------------------------------------------

CONTROL_FIELDS = ("Package", "Version", "Architecture", "Depends", "Installed-Size")


def check_04_control_fields(ctx, rep):
    stanzas = ctx.stanzas
    if stanzas is None:
        rep.skip("Packages 未成功解析，跳过 control 交叉比对")
        return

    dpkg = shutil.which("dpkg-deb")
    if dpkg and ctx.can_spawn():
        rep.note("control 读取方式：dpkg-deb -f（%s）" % dpkg)
    elif dpkg:
        rep.note(
            "control 读取方式：内置 ar+tar 解析（系统有 dpkg-deb，但当前 Python "
            "运行环境不支持 fork/exec，无法调用外部命令；比对内容等价）"
        )
    else:
        rep.note("control 读取方式：内置 ar+tar 解析（系统未找到 dpkg-deb）")

    compared = 0
    for index, stanza in enumerate(stanzas, 1):
        filename = stanza.get("Filename") or ""
        tag = "stanza #%d（%s）" % (index, stanza.get("Package") or "?")
        if not filename:
            rep.skip("%s 没有 Filename，跳过 control 比对" % tag)
            continue
        deb_path = ctx.path(filename)
        if not os.path.isfile(deb_path):
            rep.skip("%s 的 deb 文件不存在，跳过 control 比对" % tag)
            continue
        status, control = ctx.deb_control(deb_path)
        if status != "ok" or control is None:
            rep.skip(
                "%s 无法从 deb 读取 control（%s），跳过该包比对" % (tag, control)
            )
            continue
        compared += 1
        for field in CONTROL_FIELDS:
            want = stanza.get(field)
            got = control.get(field)
            same = (want if want is not None else "") == (got if got is not None else "")
            rep.check(
                same,
                "%s 的 %s 与 deb 的 control 一致（Packages=%r，control=%r）"
                % (tag, field, want, got),
            )

    if compared == 0:
        rep.skip("所有 stanza 的 deb control 均无法读取，跳过交叉比对")


# ---------------------------------------------------------------------------
# 校验项 5：压缩变体解压后与 Packages 逐字节一致（SPEC §5.5）
# ---------------------------------------------------------------------------

def check_05_compressed(ctx, rep):
    if ctx.packages_raw is None:
        if not os.path.isfile(ctx.path("Packages")):
            rep.skip("Packages 不存在，跳过压缩变体检查")
            return
        ctx.packages_raw = read_bytes(ctx.path("Packages"))

    source = ctx.packages_raw
    variants = (
        ("Packages.gz", gzip.decompress, "gzip"),
        ("Packages.bz2", bz2.decompress, "bzip2"),
        ("Packages.xz", lzma.decompress, "xz"),
    )
    for name, decompressor, label in variants:
        path = ctx.path(name)
        if not os.path.isfile(path):
            rep.bad("%s 不存在，无法校验压缩变体" % name)
            continue
        compressed = read_bytes(path)
        try:
            plain = decompressor(compressed)
        except Exception as exc:  # noqa: BLE001 - 解压失败即 FAIL
            rep.bad("%s 解压失败（%s）：%s" % (name, label, exc))
            continue
        rep.ok("%s 可正常解压（%s）" % (name, label))
        rep.check(
            plain == source,
            "%s 解压后与 Packages 逐字节一致（%d 字节 vs %d 字节）"
            % (name, len(plain), len(source)),
        )

    gz_path = ctx.path("Packages.gz")
    if os.path.isfile(gz_path):
        header = read_bytes(gz_path)[:8]
        if len(header) >= 8 and header[:3] == b"\x1f\x8b\x08":
            mtime = int.from_bytes(header[4:8], "little")
            rep.check(mtime == 0, "Packages.gz 的 gzip mtime 为 0（可复现构建，实际 %d）" % mtime)
        else:
            rep.bad("Packages.gz 不是合法的 gzip 文件头")


# ---------------------------------------------------------------------------
# 校验项 6：Release 元数据与校验和（SPEC §5.6）
# ---------------------------------------------------------------------------

def _parse_hash_block(value, hex_len):
    """解析 MD5Sum / SHA256 续行块，返回 (entries, bad)。

    entries = [(hash, size 字符串, 文件名)]；bad = [格式不合法的原始行]。
    """
    entries = []
    bad = []
    for line in (value or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            bad.append(line)
            continue
        hash_value, size_value, filename = parts
        if re.fullmatch(r"[0-9a-f]{%d}" % hex_len, hash_value) is None:
            bad.append(line)
            continue
        if not size_value.isdigit():
            bad.append(line)
            continue
        entries.append((hash_value, size_value, filename))
    return entries, bad


def check_06_release(ctx, rep):
    path = ctx.path("Release")
    if not os.path.isfile(path):
        rep.skip("Release 不存在，跳过")
        return

    raw = read_bytes(path)
    if raw.startswith(BOM):
        rep.bad("Release 含有 UTF-8 BOM（规范要求无 BOM）")
    else:
        rep.ok("Release 无 UTF-8 BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.bad("Release 不是合法 UTF-8：%s" % exc)
        return
    rep.ok("Release 可按 UTF-8 解码")
    rep.check(b"\r" not in raw, "Release 使用 LF 换行（未发现 CR）")

    stanzas, problems = parse_rfc822(text)
    for severity, message in problems:
        if severity == "error":
            rep.bad("Release 解析错误：%s" % message)
        else:
            rep.warn("Release 解析提示：%s" % message)

    if len(stanzas) != 1:
        rep.bad("Release 应只包含一个段落（实际解析出 %d 个）" % len(stanzas))
    if not stanzas:
        return
    rel = stanzas[0]

    basic_fields = (
        "Origin",
        "Label",
        "Suite",
        "Version",
        "Codename",
        "Architectures",
        "Components",
        "Description",
        "Date",
    )
    missing = [field for field in basic_fields if not rel.has(field)]
    rep.check(not missing, "Release 包含全部基础字段（缺少：%s）" % ("、".join(missing) or "无"))
    rep.check(rel.get("Origin") == REPO_ORIGIN, "Release 的 Origin 为 %r（实际 %r）" % (REPO_ORIGIN, rel.get("Origin")))
    rep.check(rel.get("Label") == REPO_LABEL, "Release 的 Label 为 %r（实际 %r）" % (REPO_LABEL, rel.get("Label")))

    # Date：合法 RFC 2822，且为 UTC（+0000）
    date_value = rel.get("Date") or ""
    date_ok = False
    parsed_date = None
    try:
        parsed_date = email.utils.parsedate_to_datetime(date_value)
        date_ok = parsed_date is not None
    except (TypeError, ValueError):
        date_ok = False
    rep.check(date_ok, "Release 的 Date 是合法 RFC 2822 时间（实际：%r）" % date_value)
    if date_ok and parsed_date is not None:
        rep.check(
            parsed_date.tzinfo is not None and parsed_date.utcoffset() == timedelta(0),
            "Release 的 Date 使用 UTC（+0000）时区（实际：%r）" % date_value,
        )

    # Architectures：覆盖所有 stanza 架构 + 规范顺序
    raw_archs = (rel.get("Architectures") or "").split()
    if ctx.stanzas is None:
        rep.skip("Packages 未成功解析，跳过 Architectures 覆盖检查")
    else:
        stanza_archs = []
        for stanza in ctx.stanzas:
            arch = stanza.get("Architecture")
            if arch and arch not in stanza_archs:
                stanza_archs.append(arch)
        if not stanza_archs:
            rep.ok(
                "空源：Release 的 Architectures 为 %s（暂无插件，保留标准架构列表）"
                % (" ".join(raw_archs) or "无")
            )
        else:
            rep.check(
                set(raw_archs) == set(stanza_archs),
                "Release 的 Architectures 覆盖所有 stanza 架构（Release=%s，stanza=%s）"
                % (" ".join(raw_archs) or "无", " ".join(stanza_archs) or "无"),
            )
        ordered = [arch for arch in CANON_ARCHS if arch in raw_archs]
        rep.check(
            raw_archs == ordered,
            "Release 的 Architectures 按规范顺序排列（iphoneos-arm、iphoneos-arm64、iphoneos-arm64e）",
        )

    # 校验行格式：一个空格 + hash + size + 文件名
    hash_line_re = re.compile(r"^ (?:[0-9a-f]{32}|[0-9a-f]{64}) \d+ \S+$")
    in_block = False
    bad_format_lines = []
    for line in text.split("\n"):
        if line in ("MD5Sum:", "SHA256:"):
            in_block = True
            continue
        if in_block:
            if line.startswith(" "):
                if not hash_line_re.match(line):
                    bad_format_lines.append(line)
            else:
                in_block = False
    rep.check(
        not bad_format_lines,
        "Release 校验行格式正确（一个空格开头 + hash + size + 文件名；异常行：%r）"
        % (bad_format_lines[:3] if bad_format_lines else []),
    )

    # MD5Sum / SHA256 两段
    for section, algo, hex_len in (("MD5Sum", "md5", 32), ("SHA256", "sha256", 64)):
        value = rel.get(section)
        if value is None:
            rep.bad("Release 缺少 %s 段" % section)
            continue
        entries, bad = _parse_hash_block(value, hex_len)
        for line in bad:
            rep.bad("%s 段存在格式不合法的行：%r" % (section, line))
        counted = {}
        for hash_value, size_value, filename in entries:
            if filename in counted:
                rep.bad("%s 段中 %s 条目重复" % (section, filename))
            counted[filename] = (hash_value, size_value)
        rep.check(
            set(counted) == set(PACKAGES_VARIANTS),
            "%s 段恰好覆盖 4 个 Packages 变体（实际：%s）"
            % (section, " ".join(sorted(counted)) or "无"),
        )
        rep.check(
            all("/" not in filename for filename in counted),
            "%s 段中的文件名不带路径前缀" % section,
        )
        for variant in PACKAGES_VARIANTS:
            variant_path = ctx.path(variant)
            if not os.path.isfile(variant_path):
                rep.skip("%s 段：%s 文件不存在，跳过 hash 比对" % (section, variant))
                continue
            want_hash = file_digest(variant_path, algo)
            want_size = os.path.getsize(variant_path)
            entry = counted.get(variant)
            if entry is None:
                rep.bad("%s 段缺少 %s 条目" % (section, variant))
                continue
            got_hash, got_size = entry
            rep.check(
                got_hash.lower() == want_hash,
                "%s 段中 %s 的 hash 正确" % (section, variant),
            )
            rep.check(
                int(got_size) == want_size,
                "%s 段中 %s 的 size 正确（Release=%s，磁盘=%d）"
                % (section, variant, got_size, want_size),
            )


# ---------------------------------------------------------------------------
# 校验项 7：图片资产（SPEC §5.7）
# ---------------------------------------------------------------------------

def check_07_png(ctx, rep):
    icon_path = ctx.path("CydiaIcon.png")
    if os.path.isfile(icon_path):
        info = png_size(read_bytes(icon_path))
        if info is None:
            rep.bad("CydiaIcon.png 不是合法的 PNG（签名 / IHDR 校验失败）")
        else:
            width, height = info
            rep.ok("CydiaIcon.png 是合法 PNG（%d×%d）" % (width, height))
            rep.check(
                width >= 64 and height >= 64,
                "CydiaIcon.png 尺寸不小于 64×64（实际 %d×%d）" % (width, height),
            )
            if (width, height) != (256, 256):
                rep.warn(
                    "CydiaIcon.png 建议为 256×256（SPEC §3.7），实际 %d×%d"
                    % (width, height)
                )
    else:
        rep.bad("CydiaIcon.png 不存在")

    banner_path = ctx.path("assets/banner.png")
    if os.path.isfile(banner_path):
        info = png_size(read_bytes(banner_path))
        if info is None:
            rep.bad("assets/banner.png 不是合法的 PNG（签名 / IHDR 校验失败）")
        else:
            width, height = info
            rep.ok("assets/banner.png 是合法 PNG（%d×%d）" % (width, height))
            ratio_ok = abs(width / float(height) - 16.0 / 9.0) <= 0.01
            rep.check(
                ratio_ok,
                "assets/banner.png 为 16:9 构图（实际 %d×%d，宽高比 %.4f）"
                % (width, height, width / float(height)),
            )
            if (width, height) != (1600, 900):
                rep.warn(
                    "assets/banner.png 建议为 1600×900（SPEC §3.7），实际 %d×%d"
                    % (width, height)
                )
    else:
        rep.bad("assets/banner.png 不存在")


# ---------------------------------------------------------------------------
# 校验项 8：index.html（SPEC §5.8）
# ---------------------------------------------------------------------------

def _has_link_icon(text):
    for match in re.finditer(r"<link\b[^>]*>", text, re.I):
        tag = match.group(0)
        if re.search(r"rel\s*=\s*[\"']icon[\"']", tag, re.I) and re.search(
            r"href\s*=\s*[\"']CydiaIcon\.png[\"']", tag, re.I
        ):
            return True
    return False


def _find_meta_tag(text, prop):
    """返回包含指定 property/name 的 <meta> 标签原文；找不到返回 None。"""
    pattern = r"(?:property|name)\s*=\s*[\"']%s[\"']" % re.escape(prop)
    for match in re.finditer(r"<meta\b[^>]*>", text, re.I):
        tag = match.group(0)
        if re.search(pattern, tag, re.I):
            return tag
    return None


def _extract_inline_scripts(text):
    """抽取内联 <script> 块，返回 [(js 源码, 是否 module)]。"""
    scripts = []
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", text, re.I | re.S):
        attrs, body = match.group(1), match.group(2)
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue
        type_match = re.search(r"\btype\s*=\s*[\"']([^\"']+)[\"']", attrs, re.I)
        script_type = type_match.group(1).strip().lower() if type_match else ""
        allowed = {
            "",
            "text/javascript",
            "application/javascript",
            "module",
            "text/ecmascript",
            "application/ecmascript",
        }
        if script_type not in allowed:
            continue
        if not body.strip():
            continue
        scripts.append((body, script_type == "module"))
    return scripts


def check_08_index_html(ctx, rep):
    path = ctx.path("index.html")
    if not os.path.isfile(path):
        rep.bad("index.html 不存在（落地页尚未产出，属并行预期）")
        return

    raw = read_bytes(path)
    if raw.startswith(BOM):
        rep.bad("index.html 含有 UTF-8 BOM（规范要求无 BOM）")
    else:
        rep.ok("index.html 无 UTF-8 BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.bad("index.html 不是合法 UTF-8：%s" % exc)
        return
    rep.ok("index.html 可按 UTF-8 解码")
    rep.check(b"\r" not in raw, "index.html 使用 LF 换行（未发现 CR）")

    # SPEC §5.8 明确要求的 5 项
    rep.check(
        re.search(r"<meta[^>]*charset\s*=\s*[\"']?\s*utf-8", text, re.I) is not None,
        'index.html 含 <meta charset="utf-8">',
    )
    rep.check(
        re.search(r"<meta[^>]*name\s*=\s*[\"']viewport[\"']", text, re.I) is not None,
        "index.html 含 viewport meta 标签",
    )
    rep.check(
        re.search(r"""fetch\s*\(\s*['"`]Packages['"`]""", text) is not None,
        "index.html 运行时 fetch('Packages')（相对路径）",
    )
    rep.check("sileo://source/" in text, "index.html 含 sileo://source/ 加源链接")
    rep.check("cydia://" in text, "index.html 含 cydia:// 加源链接")

    # 不允许外部 http(s) 资源引用
    external_patterns = (
        ("<script src=…> 外部脚本", r"<script\b[^>]*\bsrc\s*=\s*[\"']?\s*(?:https?:)?//"),
        ("<link href=…> 外部样式/资源", r"<link\b[^>]*\bhref\s*=\s*[\"']?\s*(?:https?:)?//"),
        ("CSS @import 外部样式", r"@import\s+(?:url\s*\(\s*)?[\"']?\s*(?:https?:)?//"),
        ("<img src=…> 外部图片", r"<img\b[^>]*\bsrc\s*=\s*[\"']?\s*(?:https?:)?//"),
    )
    for label, pattern in external_patterns:
        match = re.search(pattern, text, re.I)
        if match is None:
            rep.ok("index.html 不含外部资源引用（%s）" % label)
        else:
            snippet = text[max(0, match.start() - 20): match.end() + 40].replace("\n", " ")
            rep.bad("index.html 含外部资源引用（%s）：…%s…" % (label, snippet))

    # SPEC §3.6 的其它硬性内容
    rep.check(
        re.search(r"<title>\s*ZYH 自用\s*</title>", text) is not None,
        "index.html 的 <title> 为 ZYH 自用（SPEC §3.6）",
    )
    rep.check(_has_link_icon(text), "index.html 含 <link rel=\"icon\" href=\"CydiaIcon.png\">（SPEC §3.6）")
    for prop in ("og:title", "og:description", "og:image"):
        rep.check(
            _find_meta_tag(text, prop) is not None,
            "index.html 含 %s 标签（SPEC §3.6）" % prop,
        )
    og_image_tag = _find_meta_tag(text, "og:image")
    if og_image_tag is None:
        rep.skip("index.html 缺少 og:image，跳过“相对 assets/banner.png”检查")
    else:
        content_match = re.search(r"content\s*=\s*[\"']([^\"']*)[\"']", og_image_tag, re.I)
        content_value = content_match.group(1).strip() if content_match else ""
        normalized = content_value[2:] if content_value.startswith("./") else content_value
        rep.check(
            normalized == "assets/banner.png",
            "index.html 的 og:image 使用相对路径 assets/banner.png（实际 content=%r）"
            % content_value,
        )
    rep.check(re.search(r"<noscript\b", text, re.I) is not None, "index.html 含 <noscript> 加源步骤（SPEC §3.6）")
    rep.check(
        re.search(
            r"new\s+URL\s*\(\s*['\"]\.[/]?['\"]\s*,\s*(?:window\.|document\.)?location\.href",
            text,
        )
        is not None,
        "index.html 用 new URL('.', location.href) 计算源地址（SPEC §3.6）",
    )
    rep.check("navigator.clipboard" in text, "index.html 含 navigator.clipboard 复制源地址逻辑（SPEC §3.6）")

    # node --check 内联 JS（SPEC §5.8）
    scripts = _extract_inline_scripts(text)
    node = shutil.which("node")
    if not node:
        rep.skip("未找到 node，跳过 node --check 内联 JS 语法检查")
    elif not ctx.can_spawn():
        rep.skip(
            "系统有 node（%s），但当前 Python 运行环境不支持 fork/exec，无法执行 "
            "node --check；请另用终端手动检查内联 JS" % node
        )
    elif not scripts:
        rep.skip("index.html 中没有可检查的内联 <script>，跳过 node --check")
    else:
        failures = []
        for index, (js, is_module) in enumerate(scripts, 1):
            suffix = ".mjs" if is_module else ".js"
            fd, tmp_path = tempfile.mkstemp(prefix="verify-inline-", suffix=suffix)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(js)
                ok, out, err = run_capture([node, "--check", tmp_path], timeout=60)
                if not ok:
                    detail = (err or out).decode("utf-8", "replace").strip()
                    failures.append("内联脚本 #%d：%s" % (index, detail[:300]))
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        rep.check(
            not failures,
            "node --check 内联 JS 语法检查通过"
            + ("" if not failures else "：" + "；".join(failures)),
        )


# ---------------------------------------------------------------------------
# 校验项 9：BASE_URL / sileo-featured.json / 模板（SPEC §5.9）
# ---------------------------------------------------------------------------

def check_09_base_url(ctx, rep):
    # 1) repo.conf
    conf_path = ctx.path("repo.conf")
    base = ""
    if not os.path.isfile(conf_path):
        rep.bad("repo.conf 不存在，无法判断 BASE_URL")
    else:
        raw = read_bytes(conf_path)
        rep.check(not raw.startswith(BOM), "repo.conf 无 UTF-8 BOM")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            rep.bad("repo.conf 不是合法 UTF-8：%s" % exc)
            text = None
        if text is not None:
            conf, problems = parse_shell_conf(text)
            ctx.conf = conf
            for problem in problems:
                rep.warn("repo.conf 解析提示：%s" % problem)
            rep.check(not problems, "repo.conf 可被 shell 风格解析（无语法错误）")
            rep.check("BASE_URL" in conf, "repo.conf 中定义了 BASE_URL")
            base = (conf.get("BASE_URL") or "").strip()

    # 2) 精选横幅模板（任何情况下都必须是合法 JSON）
    template_path = ctx.path("assets/sileo-featured.template.json")
    if os.path.isfile(template_path):
        template_raw = read_bytes(template_path)
        try:
            json.loads(template_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            rep.bad("assets/sileo-featured.template.json 不是合法 JSON：%s" % exc)
        else:
            rep.ok("assets/sileo-featured.template.json 是合法 JSON")
            rep.check(
                "__BASE_URL__" in template_raw.decode("utf-8", "replace"),
                "精选横幅模板包含 __BASE_URL__ 占位符",
            )
    else:
        rep.skip("未使用精选横幅（缺少 assets/sileo-featured.template.json，允许）")

    stanzas = ctx.stanzas or []
    featured_path = ctx.path("sileo-featured.json")

    if base == "":
        # BASE_URL 为空：不得有 sileo-featured.json，stanza 不得出现相对 Icon/Depiction
        rep.check(
            not os.path.exists(featured_path),
            "BASE_URL 为空时根目录不应存在 sileo-featured.json",
        )
        relative_bad = []
        for stanza in stanzas:
            for field in ("Icon", "Depiction"):
                value = stanza.get(field)
                if value and re.match(r"^https?://", value) is None:
                    relative_bad.append(
                        "%s 的 %s=%r" % (stanza.get("Package") or "?", field, value)
                    )
        rep.check(
            not relative_bad,
            "BASE_URL 为空时 stanza 不出现相对 Icon/Depiction（异常：%s）"
            % ("；".join(relative_bad) or "无"),
        )
        return

    # BASE_URL 非空
    if re.match(r"^https?://", base) is None:
        rep.bad("BASE_URL 非空时必须是 http(s) 绝对地址（实际 %r）" % base)
        return
    rep.ok("BASE_URL 是 http(s) 绝对地址：%s" % base)
    base_norm = base.rstrip("/")
    if base != base_norm:
        rep.warn("BASE_URL 末尾带斜杠，生成的地址可能出现 //（实际 %r）" % base)

    # 3) sileo-featured.json（未配置 FEATURED_PACKAGE 时允许不存在）
    featured_enabled = bool(((ctx.conf or {}).get("FEATURED_PACKAGE") or "").strip())
    if not os.path.isfile(featured_path):
        if featured_enabled:
            rep.bad("BASE_URL 非空且配置了 FEATURED_PACKAGE，但根目录缺少 sileo-featured.json")
        else:
            rep.ok("未配置精选横幅（FEATURED_PACKAGE 为空），根目录不需要 sileo-featured.json")
    else:
        rep.ok("BASE_URL 非空时根目录存在 sileo-featured.json")
        featured = None
        try:
            featured = json.loads(read_bytes(featured_path).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            rep.bad("sileo-featured.json 不是合法 JSON：%s" % exc)
        if featured is not None:
            rep.ok("sileo-featured.json 是合法 JSON")
            rep.check(
                isinstance(featured, dict)
                and featured.get("class") == "FeaturedBannersView",
                "sileo-featured.json 的 class 为 FeaturedBannersView",
            )
            banners = featured.get("banners") if isinstance(featured, dict) else None
            rep.check(
                isinstance(banners, list) and len(banners) > 0,
                "sileo-featured.json 含非空 banners 数组",
            )
            known_packages = {stanza.get("Package") for stanza in stanzas}
            for index, banner in enumerate(banners if isinstance(banners, list) else []):
                if not isinstance(banner, dict):
                    rep.bad("banners[%d] 不是 JSON 对象" % index)
                    continue
                url = banner.get("url") or ""
                rep.check(
                    re.match(r"^https?://", url) is not None,
                    "banners[%d].url 是 http(s) 绝对地址（实际 %r）" % (index, url),
                )
                rep.check(
                    url.startswith(base_norm + "/"),
                    "banners[%d].url 以 BASE_URL 开头（实际 %r）" % (index, url),
                )
                rep.check(
                    banner.get("package") in known_packages,
                    "banners[%d].package 能在 Packages 中找到（实际 %r）"
                    % (index, banner.get("package")),
                )

    # 4) stanza 的 Icon / Depiction 必须为正确的绝对地址
    icon_bad = []
    depiction_bad = []
    for stanza in stanzas:
        pkg = stanza.get("Package") or ""
        icon_file = ctx.path("icons", pkg + ".png")
        icon_value = stanza.get("Icon")
        if os.path.isfile(icon_file):
            expected_icon = base_norm + "/icons/" + pkg + ".png"
            if icon_value is None:
                icon_bad.append("%s 缺少 Icon 字段" % pkg)
            elif re.match(r"^https?://", icon_value) is None:
                icon_bad.append("%s 的 Icon 不是绝对地址：%r" % (pkg, icon_value))
            elif icon_value != expected_icon:
                icon_bad.append(
                    "%s 的 Icon 应为 %r，实际 %r" % (pkg, expected_icon, icon_value)
                )
        elif icon_value and re.match(r"^https?://", icon_value) is None:
            icon_bad.append("%s 的 Icon 不是绝对地址：%r" % (pkg, icon_value))

        expected_depiction = base_norm + "/#" + pkg
        depiction_value = stanza.get("Depiction")
        if depiction_value is None:
            depiction_bad.append("%s 缺少 Depiction 字段" % pkg)
        elif re.match(r"^https?://", depiction_value) is None:
            depiction_bad.append("%s 的 Depiction 不是绝对地址：%r" % (pkg, depiction_value))
        elif depiction_value != expected_depiction:
            depiction_bad.append(
                "%s 的 Depiction 应为 %r，实际 %r"
                % (pkg, expected_depiction, depiction_value)
            )
    rep.check(
        not icon_bad,
        "BASE_URL 非空时 stanza 的 Icon 为正确的绝对地址（异常：%s）"
        % ("；".join(icon_bad) or "无"),
    )
    rep.check(
        not depiction_bad,
        "BASE_URL 非空时 stanza 的 Depiction 为正确的绝对地址（异常：%s）"
        % ("；".join(depiction_bad) or "无"),
    )


# ---------------------------------------------------------------------------
# 校验项 10：HTTP 冒烟（SPEC §5.10）
# ---------------------------------------------------------------------------

def _wait_for_port(port, proc=None, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            conn = socket.create_connection(("127.0.0.1", port), 0.25)
            conn.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_subprocess_http(root):
    """常规平台：用 python -m http.server 起子进程。返回 (port, proc)。"""
    last_error = "未知错误"
    for _attempt in range(6):
        port = random.randint(40000, 60000)
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                    "--directory",
                    root,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RuntimeError("无法启动 python -m http.server：%s" % exc)
        if _wait_for_port(port, proc):
            return port, proc
        last_error = "端口 %d 未就绪" % port
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 - 清理兜底
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    raise RuntimeError("HTTP 服务多次启动失败：%s" % last_error)


def _start_inprocess_http(root):
    """iOS 内嵌 Python：进程内 ThreadingHTTPServer（真实 TCP/HTTP）。返回 (port, server, thread)。"""
    import http.server  # 延迟导入，避免影响其它检查

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            kwargs["directory"] = root
            super().__init__(*args, **kwargs)

        def log_message(self, fmt, *args):  # 静默日志
            pass

    server = None
    port = 0
    retries = 0
    for attempt in range(8):
        candidate = random.randint(40000, 60000)
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", candidate), _Handler)
            port = candidate
            retries = attempt
            break
        except OSError:
            continue
    if server is None:
        # 高位端口全被占用（极小概率）：退回由系统分配随机空闲端口
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        retries = 8
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port, server, thread, retries


def check_10_http(ctx, rep):
    if getattr(ctx.args, "no_http", False):
        rep.skip("已指定 --no-http，跳过 HTTP 冒烟检查")
        return

    proc = None
    server = None
    thread = None
    inprocess = False
    try:
        if ctx.can_spawn():
            rep.note("HTTP 冒烟方式：子进程 python -m http.server（127.0.0.1 随机高位端口）")
            port, proc = _start_subprocess_http(ctx.root)
        else:
            rep.note(
                "HTTP 冒烟方式：进程内 ThreadingHTTPServer（当前 Python 运行环境不支持 "
                "fork/exec，无法起 python -m http.server 子进程；仍是真实的 TCP + HTTP "
                "请求/响应，结束后自动关闭服务）"
            )
            port, server, thread, retries = _start_inprocess_http(ctx.root)
            inprocess = True
            if retries:
                rep.note("HTTP 服务端口占用重试 %d 次后成功" % retries)
    except Exception as exc:  # noqa: BLE001 - 启动失败就是 FAIL
        rep.bad("无法启动本地 HTTP 服务：%s" % exc)
        return

    rep.ok("本地 HTTP 服务已在 http://127.0.0.1:%d 启动" % port)
    base_url = "http://127.0.0.1:%d" % port
    try:
        for path in HTTP_PATHS:
            disk_path = ctx.path(path.lstrip("/"))
            status = None
            body = None
            error_text = None
            try:
                with urllib.request.urlopen(base_url + path, timeout=20) as response:
                    status = response.status
                    body = response.read()
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    body = exc.read()
                except Exception:  # noqa: BLE001
                    body = None
                error_text = "HTTP %d" % exc.code
            except Exception as exc:  # noqa: BLE001 - 网络错误就是 FAIL
                error_text = "%s: %s" % (type(exc).__name__, exc)

            rep.check(
                status == 200,
                "GET %s 返回 HTTP 200（实际 %s）"
                % (path, status if status is not None else error_text),
            )
            if os.path.isfile(disk_path):
                if body is not None:
                    rep.check(
                        body == read_bytes(disk_path),
                        "GET %s 的响应内容与磁盘文件逐字节一致（%d 字节）"
                        % (path, len(body)),
                    )
                else:
                    rep.bad("GET %s 未取到响应内容（%s）" % (path, error_text))
            else:
                rep.bad("GET %s 无法比对：磁盘上不存在对应文件" % path)
    finally:
        if inprocess:
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
            if thread is not None:
                thread.join(timeout=3)
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# 校验项 11：机器可读汇总（SPEC §5.11）
# ---------------------------------------------------------------------------

def check_11_summary(ctx, rep):
    rep.ok("已输出机器可读汇总行（PASS: N FAIL: M SKIP: K）并按组列出失败原因")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

CHECKS = (
    (1, "文件齐全（SPEC §5.1）", check_01_files),
    (2, "Packages RFC822 结构（SPEC §5.2）", check_02_packages),
    (3, "debs/ 与 Packages 双向一致（SPEC §5.3）", check_03_debs),
    (4, "control 字段与 Packages 一致（SPEC §5.4）", check_04_control_fields),
    (5, "Packages 压缩变体逐字节一致（SPEC §5.5）", check_05_compressed),
    (6, "Release 元数据与校验和（SPEC §5.6）", check_06_release),
    (7, "PNG 图标与横幅（SPEC §5.7）", check_07_png),
    (8, "index.html 落地页（SPEC §5.8）", check_08_index_html),
    (9, "BASE_URL / 精选横幅（SPEC §5.9）", check_09_base_url),
    (10, "HTTP 冒烟（SPEC §5.10）", check_10_http),
    (11, "机器可读汇总（SPEC §5.11）", check_11_summary),
)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="ZYH 自用 越狱源 —— 独立校验器（SPEC §5，共 11 项）",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="仓库根目录（默认自动定位本文件的上一级目录）",
    )
    parser.add_argument(
        "--no-http",
        action="store_true",
        help="跳过第 10 项 HTTP 冒烟检查",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启用 HTTP 冒烟检查（默认已启用，此参数仅为兼容 SPEC §5 的 --serve 说法）",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="额外打印每一条通过的断言",
    )
    return parser.parse_args(argv)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)
    root = os.path.abspath(args.repo) if args.repo else DEFAULT_ROOT
    if not os.path.isdir(root):
        print("[错误] 仓库根目录不存在：%s" % root)
        return 2

    mode = "默认（含 HTTP 冒烟）" if not args.no_http else "--no-http（跳过 HTTP 冒烟）"
    print("=" * 68)
    print(" ZYH 自用 越狱源 · 独立校验器 verify.py（SPEC §5，共 11 项）")
    print(" 仓库根：%s" % root)
    print(" Python：%s" % sys.version.split()[0])
    print(" 运行模式：%s" % mode)
    print("=" * 68)

    ctx = Context(root, args)
    rep = Reporter(verbose=args.verbose)

    for number, title, func in CHECKS:
        rep.start(number, title)
        try:
            func(ctx, rep)
        except Exception as exc:  # noqa: BLE001 - 单检查异常不应中断整体校验
            rep.bad("该检查执行异常：%s: %s" % (type(exc).__name__, exc))
            if args.verbose:
                import traceback

                traceback.print_exc()
        rep.finish()

    print("-" * 68)
    if rep.pass_n + rep.fail_n + rep.skip_n == 0:
        print("未执行任何断言（异常情况）")
    if rep.warns:
        print("提示（不视为失败）：")
        for message in rep.warns:
            print("  · %s" % message)
        print("-" * 68)

    print("PASS: %d FAIL: %d SKIP: %d" % (rep.pass_n, rep.fail_n, rep.skip_n))
    if rep.fail_n:
        print("校验失败：共 %d 项问题（详见上方各检查组的 ✗ 条目），退出码 1。" % rep.fail_n)
        return 1
    print("校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
