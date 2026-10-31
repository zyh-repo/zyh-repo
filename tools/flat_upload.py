#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把标准仓库转换成「扁平上传包」：所有文件放在同一层（仓库根），不需要任何子文件夹。

用途：GitHub / iCloud 等网页上传界面无法选择文件夹，只能多选文件。
     本脚本把 debs/ 、icons/ 、assets/ 里的文件全部提到根目录，
     并同步改写 Packages 里的 Filename / Icon、sileo-featured.json 里的横幅地址、
     index.html 里的图片相对路径，重新生成压缩索引与 Release 校验和。

用法：
    ./tools/flat-upload.sh [输出目录]
    ./tools/flat-upload.sh --out /path/to/out --zip

产物：
    <输出目录>/            扁平站点（上传到仓库根即可，见其中的「上传说明.md」）
    <输出目录>.zip         同名压缩包（解压后仍是一层文件）
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
import sys
import time
import zipfile

PACKAGES_VARIANTS = ["Packages", "Packages.bz2", "Packages.gz", "Packages.xz"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class FlatError(Exception):
    pass


def die(msg):
    print("[错误] " + msg, file=sys.stderr)
    sys.exit(1)


def log(msg):
    print(msg)


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def hash_bytes(data, algo):
    h = hashlib.new(algo)
    h.update(data)
    return h.hexdigest()


def release_date():
    t = time.gmtime()
    return "%s, %02d %s %04d %02d:%02d:%02d +0000" % (
        DAYS[t.tm_wday], t.tm_mday, MONTHS[t.tm_mon - 1],
        t.tm_year, t.tm_hour, t.tm_min, t.tm_sec,
    )


def parse_conf(path):
    conf = {}
    if not os.path.isfile(path):
        return conf
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if raw[:1] in "\"'":
            quote = raw[0]
            value = raw[1:raw.index(quote, 1)] if quote in raw[1:] else raw[1:]
        else:
            value = raw.split(" #")[0].strip()
        conf[key] = value
    return conf


def parse_packages(text):
    """把 Packages 解析成有序 stanza 列表；只用于本脚本的路径改写，不参与构建。"""
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
                cur[last].append(line)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if cur is None:
            cur = {}
        cur[key] = [value.lstrip(" ")]
        last = key
    if cur:
        stanzas.append(cur)
    return stanzas


def render_packages(stanzas):
    if not stanzas:
        return ""
    chunks = []
    for stanza in stanzas:
        lines = []
        for key, values in stanza.items():
            lines.append(key + ": " + values[0])
            for extra in values[1:]:
                lines.append(extra if extra.startswith(" ") else " " + extra)
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + "\n"


def make_variants(packages_bytes):
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(packages_bytes)
    return {
        "Packages": packages_bytes,
        "Packages.bz2": bz2.compress(packages_bytes, compresslevel=9),
        "Packages.gz": gz_buf.getvalue(),
        "Packages.xz": lzma.compress(packages_bytes, format=lzma.FORMAT_XZ, preset=9),
    }


def build_release(old_text, variants):
    """保留原 Release 头部字段，替换 Date 与两段校验和。"""
    lines = old_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    head = []
    for line in lines:
        if line.startswith("Date:"):
            break
        if line.startswith("MD5Sum:"):
            break
        if line.startswith("SHA256:"):
            break
        head.append(line)
    head = [line for line in head if line.strip()]
    head.append("Date: %s" % release_date())
    out = list(head)
    for algo, label in (("md5", "MD5Sum"), ("sha256", "SHA256")):
        out.append("%s:" % label)
        for name in PACKAGES_VARIANTS:
            data = variants[name]
            out.append(" %s %d %s" % (hash_bytes(data, algo), len(data), name))
    return "\n".join(out) + "\n"


UPLOAD_README = """# 源部署包说明（扁平结构）

这是「ZYH 自用」越狱源的**可直接部署文件包**：只含越狱源本身（索引 / 落地页 / 图标 / 横幅），
**不包含任何插件**。所有文件都在同一层，上传到 GitHub 仓库根目录即可，**不需要建任何子文件夹**。

## 上传步骤

1. GitHub 新建一个 public 仓库（如 `zyh-repo`，不要勾选 README 初始化）。
2. 仓库页 → `Add file` → `Upload files` → 全选本文件夹里的**所有文件** → `Commit changes`。
3. 仓库 `Settings` → `Pages` → `Deploy from a branch` → `main` / `(root)` → `Save`。
4. 等 1–2 分钟，打开 `__BASE_URL__/` 即为源地址（此时源里还没有插件，属正常）。

## 说明

- `.nojekyll` 是隐藏文件，手机文件选择器可能看不到：**可以跳过**；
  如果 Pages 部署异常，在 GitHub 网页 `Add file` → `Create new file`，
  文件名填 `.nojekyll`（内容留空或一个空行）提交即可。
__SOURCE_STATE__
- 以后发布插件（在源仓库里操作）：

      bash tools/manage.sh add 你的插件.deb
      bash tools/build.sh
      bash tools/verify.sh
      bash tools/flat-upload.sh --zip

  再把新生成的部署包根目录里变动的文件上传覆盖即可。
- 源地址：`__BASE_URL__/`
"""


def main(argv):
    parser = argparse.ArgumentParser(description="生成 ZYH 自用 越狱源的扁平上传包")
    parser.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--out", default="")
    parser.add_argument("--zip", action="store_true", help="同时生成同名 zip")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    out = os.path.abspath(args.out or os.path.join(os.path.dirname(repo), "ZYH-源部署包"))

    conf = parse_conf(os.path.join(repo, "repo.conf"))
    base_url = (conf.get("BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        die("repo.conf 的 BASE_URL 为空。先运行 ./tools/set-url.sh \"https://<user>.github.io/<repo>\" 再生成上传包。")

    packages_path = os.path.join(repo, "Packages")
    if not os.path.isfile(packages_path):
        die("找不到 Packages，先运行 ./tools/build.sh")
    stanzas = parse_packages(read_text(packages_path))
    empty_source = not stanzas

    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    # 1) 拷贝 deb（只拷进索引的，旧版本不拷）
    deb_names = []
    for stanza in stanzas:
        filename = stanza.get("Filename", [""])[0]
        rel = filename[5:] if filename.startswith("debs/") else filename
        src = os.path.join(repo, "debs", rel)
        if not os.path.isfile(src):
            die("Packages 里的 Filename 指向不存在的文件：debs/%s" % rel)
        shutil.copy2(src, os.path.join(out, rel))
        deb_names.append(rel)

    # 2) 拷贝图标到根：icons/<Package>.png -> <Package>.png
    icon_names = []
    for stanza in stanzas:
        pkg = stanza.get("Package", [""])[0]
        icon = os.path.join(repo, "icons", pkg + ".png")
        if os.path.isfile(icon) and (pkg + ".png") not in icon_names:
            shutil.copy2(icon, os.path.join(out, pkg + ".png"))
            icon_names.append(pkg + ".png")

    # 3) 拷贝横幅 / 默认图标到根
    for name in ("banner.png", "package-icon-default.png"):
        src = os.path.join(repo, "assets", name)
        if not os.path.isfile(src):
            die("缺少 assets/%s" % name)
        shutil.copy2(src, os.path.join(out, name))

    # 4) 根文件：CydiaIcon.png / repo.conf / 文档 / .nojekyll
    for name in ("CydiaIcon.png", "README.md", "使用说明.md"):
        src = os.path.join(repo, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out, name))
    write_bytes(os.path.join(out, ".nojekyll"), b"")

    # 5) 改写 Packages：Filename 去 debs/ 前缀，Icon 去 icons/ 段
    icon_base = base_url + "/"
    for stanza in stanzas:
        if "Filename" in stanza:
            filename = stanza["Filename"][0]
            stanza["Filename"] = [filename[5:] if filename.startswith("debs/") else filename]
        if "Icon" in stanza:
            stanza["Icon"] = [stanza["Icon"][0].replace(base_url + "/icons/", icon_base)]
    packages_bytes = render_packages(stanzas).encode("utf-8")
    variants = make_variants(packages_bytes)
    for name in PACKAGES_VARIANTS:
        write_bytes(os.path.join(out, name), variants[name])

    # 6) 改写 index.html 中的相对图片路径
    index_src = os.path.join(repo, "index.html")
    if not os.path.isfile(index_src):
        die("缺少 index.html")
    html = read_text(index_src)
    html = html.replace("assets/banner.png", "banner.png")
    html = html.replace("assets/package-icon-default.png", "package-icon-default.png")
    if "assets/" in html:
        die("index.html 里仍有未处理的 assets/ 引用，请手动检查")
    write_text(os.path.join(out, "index.html"), html)

    # 7) 改写 sileo-featured.json 的横幅地址
    featured_src = os.path.join(repo, "sileo-featured.json")
    if os.path.isfile(featured_src):
        data = json.loads(read_text(featured_src))
        for banner in data.get("banners", []):
            url = banner.get("url", "")
            banner["url"] = url.replace(base_url + "/assets/", base_url + "/")
        write_text(os.path.join(out, "sileo-featured.json"),
                   json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")

    # 8) Release：沿用头部字段，重算校验和
    release_src = os.path.join(repo, "Release")
    if not os.path.isfile(release_src):
        die("缺少 Release，先运行 ./tools/build.sh")
    write_text(os.path.join(out, "Release"),
               build_release(read_text(release_src), variants))

    # 9) 部署说明
    if empty_source:
        source_state = "- 当前是**空源**：`Packages` 文件为 0 字节，Sileo 里显示空列表属正常。"
    else:
        source_state = "- 当前已收录 **%d 个插件条目**（deb 文件与图标都在本目录根下）。" % len(stanzas)
    upload_readme = (
        UPLOAD_README.replace("__BASE_URL__", base_url)
        .replace("__SOURCE_STATE__", source_state)
    )
    write_text(os.path.join(out, "部署说明.md"), upload_readme)

    # 10) 自检
    errors = []
    for name in PACKAGES_VARIANTS:
        path = os.path.join(out, name)
        data = open(path, "rb").read()
        if name == "Packages.gz":
            expanded = gzip.decompress(data)
        elif name == "Packages.bz2":
            expanded = bz2.decompress(data)
        elif name == "Packages.xz":
            expanded = lzma.decompress(data)
        else:
            expanded = data
        if expanded != packages_bytes:
            errors.append("%s 解压后与 Packages 不一致" % name)
    for stanza in stanzas:
        rel = stanza["Filename"][0]
        if not os.path.isfile(os.path.join(out, rel)):
            errors.append("缺少 deb 文件：" + rel)
        icon = stanza.get("Icon", [""])[0]
        if icon:
            icon_name = icon.rsplit("/", 1)[-1]
            if not os.path.isfile(os.path.join(out, icon_name)):
                errors.append("缺少图标文件：" + icon_name)
    release_text = read_text(os.path.join(out, "Release"))
    if base_url not in read_text(os.path.join(out, "index.html")):
        pass  # index.html 用运行时地址，不一定含 BASE_URL
    if os.path.isfile(os.path.join(out, "sileo-featured.json")):
        featured = json.loads(read_text(os.path.join(out, "sileo-featured.json")))
        for banner in featured.get("banners", []):
            if not banner.get("url", "").startswith("http"):
                errors.append("sileo-featured.json 横幅地址不是绝对地址")
            elif banner["url"].rsplit("/", 1)[-1] == "banner.png":
                if not os.path.isfile(os.path.join(out, "banner.png")):
                    errors.append("缺少 banner.png")
    if errors:
        print("[错误] 上传包自检失败：", file=sys.stderr)
        for item in errors:
            print("  - " + item, file=sys.stderr)
        sys.exit(1)

    zip_path = ""
    if args.zip:
        zip_path = out + ".zip"
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name in sorted(os.listdir(out)):
                zf.write(os.path.join(out, name), name)
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                die("zip 校验失败：" + bad)

    log("[完成] 源部署包（%s）：%s" % ("空源，未见插件" if empty_source else "含 %d 个插件" % len(stanzas), out))
    log("  文件数：%d 个（deb %d / 图标 %d）" % (len(os.listdir(out)), len(deb_names), len(icon_names)))
    log("  源地址：%s/" % base_url)
    log("  上传方式：GitHub 仓库 → Add file → Upload files → 全选该目录所有文件")
    if zip_path:
        log("  压缩包：%s（%d 字节）" % (zip_path, os.path.getsize(zip_path)))
    return 0


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except FlatError as exc:
        die(str(exc))
