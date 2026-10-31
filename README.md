# ZYH 自用 越狱源

> 个人自用的 iOS 越狱源（flat apt 源），Sileo / Zebra / Cydia 均可添加。
> 本仓库根目录 = 站点根目录，推上 GitHub Pages（或任意静态托管）即可当源使用。

- 源显示名：`ZYH 自用`
- 源地址：`https://zyh-repo.github.io/zyh-repo/`
- 源格式：扁平（flat）apt 源，无签名、不收费，只收录自己用的插件与工具
- 当前收录：以源页面（落地页 / Sileo）实时列表为准，维护者按需发布
- 给最终用户的加源 / 安装说明见 [`使用说明.md`](使用说明.md)

---

## 快速开始

```bash
# 0) 先看看源里现在有哪些插件（查看 / 详情 / 删除 / 清理都走 manage.sh）
./tools/manage.sh list

# 1) 发布一个（或多个）新 deb：拷贝进 debs/ 并重建索引
./tools/publish.sh path/to/pkg.deb

# 2) 设置源托管地址（GitHub Pages 地址）
./tools/set-url.sh "https://zyh-repo.github.io/zyh-repo"

# 3) 校验通过后推送，等 1-2 分钟 GitHub Pages 自动生效
./tools/verify.sh
git push        # 首次推送前先按「GitHub Pages 部署」章节配好 remote
```

最终源地址就是 `https://zyh-repo.github.io/zyh-repo/`（给用户加源用这个，不要带 `Packages`）。

插件增删改查统一用 `./tools/manage.sh`（列表 / 详情 / 添加 / 删除 / 清理旧版本），详见「日常操作 → 0. 管理已有插件」。

> 小提示：如果直接执行 `./tools/xxx.sh` 报 `bad interpreter` 或 `No such file or directory`，说明当前环境没有 `/bin/bash`、`/usr/bin/env` 这类解释器路径（个别手机 App 内置 shell 会这样）。此时在命令前加 `bash ` 即可，例如 `bash tools/build.sh`、`bash tools/verify.sh`，效果完全一样。

---

## 仓库结构

```text
ZYH-Repo/                          # 仓库根 = 站点根
├── .nojekyll                      # 空文件：让 GitHub Pages 跳过 Jekyll 处理
├── CydiaIcon.png                  # 源图标 256×256
├── Packages                       # apt 索引（自动生成，勿手改）
├── Packages.gz                    # Packages 的 gzip 压缩版（自动生成）
├── Packages.bz2                   # Packages 的 bzip2 压缩版（自动生成）
├── Packages.xz                    # Packages 的 xz 压缩版（自动生成）
├── Release                        # apt 源元数据 + 四个索引的哈希（自动生成）
├── sileo-featured.json            # Sileo 精选横幅，仅当 repo.conf 配置了 FEATURED_PACKAGE 时生成（可选）
├── index.html                     # 落地页：包列表 +「添加到 Sileo」按钮
├── repo.conf                      # 源配置：显示名、BASE_URL、精选横幅开关（可手改）
├── sync-sources.txt               # 同步 / 备份源配置（mirror.sh sync 读取，可手改）
├── README.md                      # 本文件（维护者视角）
├── 使用说明.md                    # 给最终用户的加源 / 安装说明
├── debs/                          # 所有 deb 放这里（索引的唯一数据来源）
│   └── （发布插件时放这里，如 com.example.myplugin_1.0_iphoneos-arm64.deb）
├── icons/                         # 每个包一个图标：icons/<Package>.png
│   └── （每个包一张 256×256 图标，如 com.example.myplugin.png）
├── assets/                        # 页面素材
│   ├── banner.png                 #   1600×900 页面横幅（16:9，源通用视觉，不含具体插件）
│   └── package-icon-default.png   #   256×256 默认包图标
└── tools/
    ├── build.sh                   # 重新生成全部索引（build.py 的入口）
    ├── build.py                   # 索引生成器（内部实现，一般不用直接调用）
    ├── publish.sh                 # 发布 deb：拷入 debs/ 并重建
    ├── manage.sh                  # 插件管理：list / info / add / remove / prune / icon
    ├── phone-publish.sh           # 手机端一键发布插件：重建+校验+生成部署包+列出要上传的文件
    ├── mirror.sh                  # 同步 / 备份其他源的插件（list / pull，可 --publish 导入本源）
    ├── mirror.py                  # 同步工具实现（分块下载 + 哈希校验）
    ├── flat-upload.sh             # 生成「扁平上传包」：所有文件同一层，网页上传不用建文件夹
    ├── flat_upload.py             # 扁平上传包生成器（内部实现）
    ├── set-url.sh                 # 设置 BASE_URL 并重建
    ├── serve.sh                   # 本地起 HTTP 服务预览
    ├── _python.sh                 # 探测可用的 Python 3（被其它脚本 source）
    ├── verify.sh                  # 校验入口
    └── verify.py                  # 独立校验器实现
```

**手改 / 自动生成约定**

| 文件 | 谁生成 | 能否手改 |
| --- | --- | --- |
| `Packages`、`Packages.gz`、`Packages.bz2`、`Packages.xz`、`Release` | `./tools/build.sh` | 不要手改，下次构建会被覆盖 |
| `sileo-featured.json` | `./tools/build.sh`（仅在配置了 `FEATURED_PACKAGE` 时） | 不要手改，改 `repo.conf` |
| `repo.conf` | 手工维护 | 可以改，改完跑一次 `./tools/build.sh` |
| `debs/`、`icons/`、`assets/`、`index.html` | 手工维护 | 可以；deb 用 `manage.sh add`（或 `publish.sh`）发布，删除用 `manage.sh remove` |
| `README.md`、`使用说明.md` | 手工维护 | 可以 |

> 只要 `debs/` 里的 deb 变了，就必须重新构建索引（`publish.sh` 会自动构建），否则 Sileo 看不到新包。

---

## 日常操作

### 0. 查看 / 管理已有插件（manage.sh）

日常增删查改统一走 `tools/manage.sh`，不用记零散命令：

```bash
./tools/manage.sh list                     # 列出源里所有插件（包名/名称/版本/架构/大小/deb 文件）
./tools/manage.sh info com.example.myplugin  # 看某个插件在索引里的完整字段
./tools/manage.sh icon com.example.myplugin  # 看该插件的图标文件是否存在
./tools/manage.sh add path/to/pkg.deb      # 添加 / 发布 deb（等同 publish.sh）
./tools/manage.sh remove com.example.myplugin                         # 删除整个包（所有架构），二次确认
./tools/manage.sh remove -y com.example.myplugin_1.0_iphoneos-arm64.deb # 只删某个架构，-y 跳过确认
./tools/manage.sh prune                    # 清理未进索引的旧版本 deb（同包同架构只保留最高版本）
```

- 任何 `add` / `remove` / `prune` 都会自动重建索引（等价于再跑一次 `./tools/build.sh`）。
- `list` 会额外提示两类可清理文件：**未进索引的 deb**（旧版本 / 重复文件，用 `prune` 清理）和**孤立图标**（`icons/` 里没有对应包的 png，可手动删除）。
- 删除插件不会自动删 `icons/<Package>.png`，按 `list` 的提示手动删即可。
- 想改插件的**显示名 / 描述 / 依赖**：这些字段写死在 deb 的 `control` 里，改 `Packages` 没用。用 `dpkg-deb -R` 解包 → 改 `DEBIAN/control` → `dpkg-deb -b` 重新打包 → `./tools/manage.sh add 新包.deb`。
- 管理工具只依赖仓库本身，不需要 `dpkg-deb`；`add` 时会校验是不是合法 `.deb`。

### 1. 发布新的 deb

```bash
./tools/publish.sh path/to/pkg.deb
./tools/publish.sh path/to/a.deb path/to/b.deb path/to/c.deb   # 一次发多个
```

`publish.sh` 会：检查文件是合法 `.deb` → 拷贝进 `debs/` → 调用 `build.py --verbose` 重建 `Packages` / `Packages.gz` / `Packages.bz2` / `Packages.xz` / `Release`（以及配置了精选横幅时的 `sileo-featured.json`）：

```text
[发布] pkg.deb → debs/
（随后打印每个包的架构、版本等明细）
```

发布前想先看看生成结果，可以直接跑：

```bash
./tools/build.sh --verbose        # 打印每个包的明细
./tools/build.sh                  # 安静重建（幂等，可反复跑）
```

**用 App 自带的 `deb_publish` 工具也可以发 deb**：它会把 deb 放进本地源目录，并更新 `Packages` 里的大小 / MD5 / SHA256。但它不会补全 `Icon`、`Depiction` 等字段，也不会重新生成 `Packages.gz` / `Packages.bz2` / `Packages.xz` / `Release` / `sileo-featured.json`。所以用 `deb_publish` 之后**必须再跑一次**：

```bash
./tools/build.sh
```

否则会得到「索引与压缩索引不一致 / Release 哈希对不上」的坏源。

**关于版本与架构**

- 同一个 `Package` + 同一个 `Architecture` 出现多个版本时，只有**版本最高**的那个会进索引；旧 deb 仍留在 `debs/` 里，但不会出现在 Sileo 中（构建时会打印警告）。升级后建议把旧 deb 删掉，保持 `debs/` 干净。
- 建议每个插件都同时提供三个架构（同一个 `Package` 名，不同 `Architecture` 字段），Sileo 会自动按设备架构展示对应版本：
  - `iphoneos-arm`：rootful（有根越狱）
  - `iphoneos-arm64`：rootless / 无根（Dopamine 等）
  - `iphoneos-arm64e`：roothide
- 一个 deb 的架构看 `dpkg-deb -f pkg.deb Architecture`；`Filename`、`Size`、`MD5sum`、`SHA256` 由构建脚本自动算，不用手填。
- `Description` 必须是 control 的**最后一个字段**（通常多行，续行以一个空格开头）；其余字段由 `dpkg-deb -f` 原样读出后追加 `Filename` / `Size` / `MD5sum` / `SHA256` 等。

### 2. 手机端一键发布插件（含上传清单）

人在手机上、只有 deb 文件时，用这个脚本一条命令走完「发布 → 重建索引 → 校验 → 生成部署包 → 列出需要上传到 GitHub 的文件」：

```bash
bash tools/phone-publish.sh 你的插件.deb                  # 发布（自动重建 + 校验 + 生成部署包）
bash tools/phone-publish.sh --icon 图标.png 你的插件.deb   # 顺带设置插件图标（256×256 PNG）
bash tools/phone-publish.sh --init                         # 首次部署后记录「已上传」快照（只需一次）
bash tools/phone-publish.sh --full 你的插件.deb            # 强制列出部署包里的全部文件
```

执行完会输出类似：

```text
==================================================================
 需要上传到 GitHub 的文件（仓库根 → Add file → Upload files 全选）
==================================================================
  [新增] com.example.myplugin_1.0_iphoneos-arm64.deb
  [更新] Packages
  [更新] Packages.gz
  [更新] Packages.bz2
  [更新] Packages.xz
  [更新] Release
  [删除] com.example.myplugin_0.9_iphoneos-arm64.deb   ← 在 GitHub 仓库里手动删除这个文件
```

然后去 GitHub 仓库根 → **Add file → Upload files** → 选中上面列出的文件 → **Commit changes**；等 1–2 分钟，Sileo 下拉刷新即可看到新插件。

- 快照保存在仓库上一级的 `ZYH-部署状态.json`，记录上次生成（也就是你上传）的文件清单；如果某次生成后没有上传，下次加 `--full` 重新列全量。
- 插件升级：把新版本 deb 再发一次即可；旧版本 deb 会提示从 GitHub 删除（源里只保留最高版本）。想顺手清理本地旧 deb，用 `bash tools/manage.sh prune`。
- 图标可选：`--icon` 传一张 256×256 PNG；没有图标也能安装，Sileo 里显示默认图标。
- deb 文件放在手机任意位置都行（脚本接受完整路径，如 `/var/mobile/Downloads/xxx.deb`）。
- 发布成功后落地页/Sileo 会出现该插件；校验不过时脚本会中止，不会生成部署包，避免上传坏源。

### 3. 同步 / 备份其他源的插件（mirror.sh）

把别的越狱源里的 deb 备份下来（可选导入自己的源）。先看列表，再决定备份谁：

```bash
# ① 只看目标源里有哪些插件（只读，不下载）
bash tools/mirror.sh list "https://repo.chariz.com" --limit 20
bash tools/mirror.sh list "https://havoc.app" --filter carplay

# ② 备份到本地（默认只备份，不发布）
bash tools/mirror.sh pull "https://repo.chariz.com" 包名 [更多包名 ...]
bash tools/mirror.sh pull "https://havoc.app" --all              # 整源备份
bash tools/mirror.sh pull "https://repo.chariz.com" 包名 --arch iphoneos-arm64

# ③ 备份并导入自己的源（重建索引 + 校验 + 生成部署包上传清单）
bash tools/mirror.sh pull "https://repo.chariz.com" 包名 --publish
```

- 源地址支持源根（自动找 `Packages` / `.xz` / `.bz2` / `.gz`）或 `Packages` 完整地址。
- 默认只备份每个包**每个架构的最新版本**；要连旧版本一起备份加 `--all-versions`。
- 备份目录：`插件备份/<源域名>/`，包含 deb（保留原 `Filename` 相对路径）+ 索引快照 `Packages` + `manifest.md/.json`（记录版本、架构、大小、哈希、下载状态）。
- 下载后按该源索引里的 `Size` / `MD5sum` / `SHA256` 校验；不完整/被篡改会判失败，并列出失败 URL 供重试。
- 大索引/大 deb 采用**分块 Range 续传**下载，规避部分网络环境截断长连接的问题。
- `--publish`：deb 拷进 `debs/`、源里有绝对 `Icon` 地址的图标拷进 `icons/` → 自动 `build.py` + `verify.sh` + 生成部署包并列出上传清单。
- 其它参数：`--out DIR` 指定备份目录、`--dry-run` 只看会下载什么、`--force` 重新下载、`--no-icons` 不导图标。
- ⚠️ 版权提醒：**备份自用没问题，但把别人源里的插件公开再分发可能侵权**（尤其付费 / 授权插件）；另外，备份的插件如果依赖别的源里的包，安装时那个源仍需保留。

### 3.1 批量增量同步（关注列表 sync）

把常备份的源写进仓库根目录的 `sync-sources.txt`（每行一个源，可带包名或 `--all`）：

```text
# 源地址                     包名/参数
https://repo.chariz.com     com.laughingquoll.prefixui
https://havoc.app           com.leftyfl1p.carplay
https://repo.dynastic.co    --all
```

然后一条命令增量更新全部：

```bash
bash tools/mirror.sh sync              # 只备份：新版本自动下载，已存在跳过
bash tools/mirror.sh sync --publish    # 备份 + 导入自己的源 + 重建索引 + 校验 + 上传清单
```

**会全自动吗？** 不会定时自动跑，但每次运行 `sync` 都是增量：
- 上游发了新版本 → 下次 `sync` 自动下载新版本（备份目录里旧版本保留）；
- 同一版本已存在（哈希一致）→ 跳过，不重复下载；
- 上游删包 / 降版本 **不会**自动删除你本地或已发布的包，需要时用 `bash tools/manage.sh remove <包名>` 手动处理。

想要「定时全自动」有三种做法：

1. **电脑定时任务**（推荐、最省事）：Windows 计划任务 / macOS `launchd` / Linux `cron` 定时执行 `bash tools/mirror.sh sync --publish`，再 `git add/commit/push` 或上传软件包。
2. **手机快捷指令**：用支持运行脚本的 App（如 a-Shell / Working Copy）配合「快捷指令」定时触发同一命令。
3. **GitHub Actions**：把仓库切成「源码结构」（含 `tools/`、`debs/`、`icons/`），加一个定时 workflow 在云端每天跑 `sync --publish` 并自动提交，Pages 自动更新。需要的话我可以帮你迁移并写好 workflow。

> 在证书正常的环境（电脑 / GitHub Actions）里可设环境变量 `MIRROR_VERIFY_SSL=1` 强制校验证书。

### 4. 设置托管地址（BASE_URL）

```bash
./tools/set-url.sh "https://zyh-repo.github.io/zyh-repo"
```

等价写法：

```bash
./tools/build.sh --set-base-url "https://zyh-repo.github.io/zyh-repo"
```

也可以直接改 `repo.conf` 里的：

```ini
BASE_URL="https://zyh-repo.github.io/zyh-repo"
```

保存后跑 `./tools/build.sh` 生效。

**BASE_URL 决定了什么**

- 非空时：每个包会带上 `Icon: <BASE_URL>/icons/<Package>.png` 和 `Depiction: <BASE_URL>/#<Package>`，Sileo 里才有包图标和「描述/截图」入口。
- 为空时：不写 `Icon` / `Depiction`。表现是 Sileo 里包没有图标，但**不影响加源和安装**（落地页会用 `assets/package-icon-default.png` 兜底）。
- 精选横幅默认关闭：`repo.conf` 里 `FEATURED_PACKAGE` 填包名后，`build.sh` 才会生成 `sileo-featured.json`（Sileo 首页横幅）。不需要就留空。
- 换域名 / 换托管方式后，要重新 `./tools/set-url.sh "新地址"` 并重新部署，否则图标和横幅还是旧地址。

注意：`BASE_URL` 是给 apt 索引和 Sileo 用的绝对地址；落地页 `index.html` 里的「添加到 Sileo」按钮是运行时按当前访问地址自动算的，不依赖 BASE_URL。

### 5. 本地测试

```bash
./tools/serve.sh            # 默认 8080
./tools/serve.sh 9000       # 指定端口
```

终端会打印本机地址和局域网地址，例如：

```text
 本机地址 : http://127.0.0.1:8080/
 局域网   : http://192.168.x.x:8080/
 Sileo 加源: http://192.168.x.x:8080/   （手机与电脑需同一 Wi-Fi）
```

- 在电脑上跑 `serve.sh`：电脑浏览器开 `http://127.0.0.1:8080/` 看落地页；手机与电脑连同一个 Wi-Fi 时，可以把终端打印的局域网地址直接加进 Sileo 做真机测试。
- 在手机上跑 `serve.sh`：同一台设备浏览器开 `http://127.0.0.1:8080/` 即可预览。
- 重点看：落地页包卡片是否正常、`/Packages` 能打开、`/CydiaIcon.png` 能加载。
- `index.html` 是用 `fetch('Packages')` 加载索引的，用 `file://` 直接打开会被浏览器拦截，所以本地预览一定走 `serve.sh`。
- 预览完按 `Ctrl+C` 停止服务。

### 6. 校验

```bash
./tools/verify.sh
```

校验器（`tools/verify.py`，独立实现、不复用构建代码）会逐项检查并输出 PASS/FAIL，最后给出 `PASS: n FAIL: m` 汇总，任何一项失败退出码非 0，可以直接用在发布前检查或 CI 里。检查范围：

1. 关键文件齐全：`.nojekyll`、`Packages`、`Packages.gz`、`Packages.bz2`、`Packages.xz`、`Release`、`CydiaIcon.png`、`index.html`、`repo.conf`、`debs/*.deb`
2. `Packages` 的 stanza 必需字段完整、格式合法（含 Description 续行）
3. `debs/` 里每个 deb 与索引一一对应，`Size` / `MD5sum` / `SHA256` 与实际文件一致
4. stanza 的 `Package` / `Version` / `Architecture` / `Depends` / `Installed-Size` 与 `dpkg-deb -f` 一致
5. `Packages.gz` / `.bz2` / `.xz` 解压后与 `Packages` 逐字节一致
6. `Release` 字段与 `Origin` / `Label` / `Architectures` / `Date` 正确，四个索引的哈希与大小正确
7. `CydiaIcon.png` 合法且足够大；`assets/banner.png` 合法且为 16:9
8. `index.html` 必备内容（viewport / charset / fetch / sileo:// / cydia://）齐全，且不引用任何外部 http(s) 资源
9. BASE_URL 为空 / 非空两种状态下 Icon、Depiction、精选横幅都符合预期（未配置 `FEATURED_PACKAGE` 时允许没有 `sileo-featured.json`）
10. 本地真实 HTTP 冒烟：起临时服务 GET `/Packages`、`/Release`、`/CydiaIcon.png`、`/index.html`，状态 200 且内容与磁盘一致

### 7. 构建产物说明

| 产物 | 作用 |
| --- | --- |
| `Packages` | apt 索引。所有 deb 的 control 字段 + `Filename` / `Size` / `MD5sum` / `SHA256`（BASE_URL 非空时还有 `Icon` / `Depiction`），`Description` 在最后。Sileo 主要读它 |
| `Packages.gz` / `Packages.bz2` / `Packages.xz` | 同一份索引的三种压缩格式，解压后与 `Packages` 逐字节相同，供不同 apt 客户端选择 |
| `Release` | 源元数据（显示名、Suite、架构列表、生成时间）+ 四个索引文件的 MD5/SHA256 与大小；客户端用它校验索引是否完整 |
| `sileo-featured.json` | Sileo 首页精选横幅配置，仅在 `repo.conf` 配置了 `FEATURED_PACKAGE` 时生成（未配置时根目录不应存在该文件） |
| `CydiaIcon.png` | 源图标，Sileo/Zebra/Cydia 的源列表里显示 |
| `icons/<Package>.png` | 每个包的图标，需要 BASE_URL 非空才会写进索引 |
| `assets/banner.png` | 落地页横幅图（1600×900，16:9，源通用视觉） |
| `index.html` | 落地页：包卡片列表、搜索、下载 deb 直链、「添加到 Sileo」/「复制源地址」/ 添加到 Cydia 按钮 |

构建是可复现的：同样的输入连续跑两次 `./tools/build.sh`，`Packages` 与 `Release` 除 `Release` 里的生成时间 `Date` 外应完全一致。

---

## GitHub Pages 部署（详细步骤）

本仓库根目录就是站点根目录，GitHub Pages 用「分支 + `/ (root)`」的方式发布即可，不需要任何构建命令。

### 0. 准备

- 一个 GitHub 账号。
- 一个仓库，已按 `zyh-repo` 命名配置（地址即 `https://zyh-repo.github.io/zyh-repo/`；换名字的话改 `repo.conf` 的 `BASE_URL` 后重建）。
- 免费账号的 Pages 只支持 **public** 仓库（私有仓库要 GitHub Pro，见 FAQ）。
- 推送前确认仓库根里**已经有生成好的索引**（`Packages`、`Release` 等）；没有就先 `./tools/build.sh`。
- 注意上传 / 推送的应该是**仓库根这一层的内容**：站点根下要直接看到 `index.html`、`Packages`、`tools/`，不要外面再套一层 `ZYH-Repo/`。

部署方式任选一种即可，效果一样；**网页上传不了文件夹**时看方式 D。

### 方式 A：网页端上传（不装任何工具，最快）

1. 打开 GitHub → 右上角「+」→ **New repository** → Repository name 填 `zyh-repo` → 选 **Public** → **Create repository**。
2. 在仓库页点 **Add file → Upload files**，把仓库根里的**全部文件和目录**（`index.html`、`Packages`、`Release`、`debs/`、`icons/`、`assets/`、`tools/`、`CydiaIcon.png`、`repo.conf`、两个 md 文件等）拖进上传区域 → **Commit changes**。
   - 注意：网页的「选择文件」对话框**只能选文件、不能选文件夹**；要保留 `debs/`、`icons/`、`assets/`、`tools/` 这些子目录，必须用**拖拽文件夹**的方式（桌面 Chrome / Edge / Safari 支持）。
   - 在手机浏览器或无法拖拽的环境里，请改用下面的 **方式 D：源部署包**，不用建任何文件夹。
3. 检查 `.nojekyll` 是否上传成功（很多系统的拖拽上传会忽略以 `.` 开头的隐藏文件）。如果没有：仓库页 **Add file → Create new file**，文件名填 `.nojekyll`，内容留空，直接 **Commit**。
4. 打开 **Settings → Pages**：
   - **Source** 选 **Deploy from a branch**
   - **Branch** 选 `main`，目录选 **`/ (root)`**
   - 点 **Save**
5. 等 1–2 分钟（首次部署可能更久），刷新 Settings → Pages，上方出现「Your site is live at https://…」即成功。

### 方式 B：桌面 git 推送（后续更新最顺）

在仓库根目录（含 `index.html`、`Packages`、`tools/` 的那一层）执行：

```bash
git init
git add -A
git commit -m "ZYH 自用 越狱源"
git branch -M main
git remote add origin https://github.com/zyh-repo/zyh-repo.git
git push -u origin main
```

之后每次更新（发布 deb、改配置、改页面）：

```bash
./tools/build.sh
./tools/verify.sh
git add -A && git commit -m "publish: 新 deb" && git push
```

推送后同样去 **Settings → Pages** 按方式 A 第 4 步设好分支和 `/ (root)`（只需设一次）。

### 方式 C：iPhone 上用 Working Copy

1. 先在 GitHub 网页建好空仓库（Public）。
2. App Store 安装 **Working Copy**，Clone 该仓库到手机上。
3. 把仓库根里的文件拷进 Working Copy 的仓库目录（可从「文件」App 拖入，或先在手机上 `./tools/build.sh` 生成索引）。
4. 在 Working Copy 里 Commit，然后 Push（免费版可克隆、拉取，**Push 需要内购解锁 Pro**）。
5. 同样去 **Settings → Pages** 设置 `main` + `/ (root)`。

> 手机上也可以直接用本仓库的 `tools/` 脚本：`./tools/publish.sh ...`、`./tools/build.sh`、`./tools/verify.sh`；如果手机终端没有 `bash` 在标准路径，改成 `bash tools/build.sh` 这种写法调用（见文首小提示）。

### 方式 E：GitHub Actions 全自动同步（一次配置，以后不用管）

云端每天定时：按 `sync-sources.txt` 关注列表拉取插件 → 校验 → 导入本源 →
重建索引 → 自动提交，GitHub Pages 自动更新。全部在 GitHub 上完成，手机网页即可配置。

**首次配置（约 3 分钟，只做一次）**

1. 把 `zyh-sync.zip`（源工具包，在仓库上一级目录）上传到 GitHub 仓库**根目录**；
2. 仓库页 **Add file → Create new file**，文件名填 `.github/workflows/sync.yml`，
   内容粘贴 `tools/gh-actions/sync.yml` 的全文（GitHub 会自动创建文件夹）；
3. **Settings → Actions → General → Workflow permissions** 选 **Read and write permissions** → Save；
4. **Actions** 标签 → 左侧「ZYH 自动同步插件」→ **Run workflow** 手动跑一次验证。

**日常使用**

- 改关注列表：直接在仓库网页编辑 `sync-sources.txt`（不用再传 zip）。
- 每天北京时间 04:00 自动跑一次；想立刻同步就到 Actions 手动 Run workflow。
- 某插件只想备份不发布：对应行行尾加 `--backup-only`。
- 同步失败会在 Actions 日志里标红并列出失败源/URL，不影响已成功的包。

> 流程说明：workflow 解压 `zyh-sync.zip` 得到源码树 → 跑 `tools/mirror.sh sync --publish`
> （下载→Size/MD5/SHA256 校验→导入 debs/→重建索引→`verify.sh` 全量校验）
> → `tools/flat-upload.sh` 生成扁平部署文件覆盖仓库根 → 有变化才自动 commit+push。
> 证书环境由 GitHub 提供（`MIRROR_VERIFY_SSL=1`），本机模拟测试已通过。

### 方式 D：源部署包（网页上传不支持文件夹时用这个）

网页上传界面只能选文件、不能选文件夹，手机浏览器尤其如此。解决办法：把源转成「所有文件都在同一层」的部署包（只含源本身，不含任何插件）。

```bash
bash tools/flat-upload.sh --zip
```

生成两个东西（都在仓库的**上一级目录**）：

- `ZYH-源部署包/`：扁平站点目录，里面所有文件都在同一层；
- `ZYH-源部署包.zip`：同内容的压缩包（解压后仍是一层文件）。

上传步骤：

1. GitHub 新建空仓库（Public，不初始化 README）。
2. 仓库页 **Add file → Upload files** → 进入解压后的 `ZYH-源部署包/`，**全选所有文件**（不需要建任何子文件夹）→ **Commit changes**。
3. `.nojekyll` 是隐藏文件，手机文件选择器可能看不到：**可以跳过**；若 Pages 部署异常，用 **Add file → Create new file** 新建文件名 `.nojekyll`（内容留空）提交即可。
4. **Settings → Pages** 按方式 A 第 4 步设置 `main` + `/ (root)`。
5. 打开 `https://zyh-repo.github.io/zyh-repo/` 验证（见下节）。

> 扁平包里 `Packages` 的 `Filename` 是 `xxx.deb`（没有 `debs/` 前缀）、`Icon` 指向根目录的 `<Package>.png`，`Release` 校验和已同步重算，`index.html` 的图片路径也改成了根目录，直接传就能用。
> 以后更新：先在标准仓库里 `bash tools/build.sh` 发布/重建，再跑一次 `bash tools/flat-upload.sh --zip`，把变动文件重新上传覆盖即可。

### 验证部署

部署完成后依次确认：

1. `https://zyh-repo.github.io/zyh-repo/Packages` —— 浏览器里应看到 apt 索引纯文本。**空源时该文件是 0 字节**，能打开（HTTP 200）就说明源可用；发布插件后这里会出现 `Package: com.example.myplugin` 开头的段落。
2. `https://zyh-repo.github.io/zyh-repo/Release` —— 应看到 `Origin: ZYH 自用` 等字段。
3. `https://zyh-repo.github.io/zyh-repo/` —— 落地页正常显示包列表与「添加到 Sileo」按钮。
4. `https://zyh-repo.github.io/zyh-repo/CydiaIcon.png` —— 源图标能显示。
5. 本仓库的 `BASE_URL` 已按 `https://zyh-repo.github.io/zyh-repo` 配置好，直接部署即可；以后换用户名 / 仓库名或托管方式时，运行 `./tools/set-url.sh "新地址"` 重建后重新推送一次。

### HTTPS

- Settings → Pages 里有 **Enforce HTTPS** 开关，建议勾选（Sileo 添加 https 源最稳）。
- 刚部署完证书签发要一点时间，勾选框可能是灰色，等几分钟刷新页面再勾。
- 勾上后必须用 `https://` 地址加源；`./tools/set-url.sh` 里的地址也要用 `https://`。

### `.nojekyll` 是干什么的

GitHub Pages 默认会把仓库当 Jekyll 静态站点跑一遍构建。本项目**不需要** Jekyll，放一个空的 `.nojekyll` 在仓库根可以禁用这套处理，保证 `Packages`、`Release`、`Packages.gz` 等文件原样发布，也避免 Jekyll 因特殊文件名或内容报错导致页面 404。这个文件必须放在站点根（和 `index.html` 同一层）。

### 替代托管（一句话）

**Cloudflare Pages** 或 **Netlify**：把本仓库目录接到平台，构建命令留空（纯静态）、发布/输出目录填 `/`（仓库根），即可得到带 HTTPS 的站点；部署完记得把 `BASE_URL` 换成新域名再重建、推送。任何能原样托管静态文件的 HTTP(S) 空间（对象存储 + CDN、自己的服务器等）也都可以，只要根目录能访问到 `Packages`。

---

## FAQ

### 1. 打开 `https://zyh-repo.github.io/zyh-repo/Packages` 是 404

按顺序排查：

- Pages 还没部署完：Settings → Pages 看状态，首次部署等 1–2 分钟甚至更久，Actions 里能看到 `pages build and deployment` 任务。
- Pages 配置不对：Source 要选 **Deploy from a branch**，Branch 选 `main`，目录选 **`/ (root)`**。
- 文件位置不对：仓库根必须**直接**有 `Packages`（如果页面上路径是 `/ZYH-Repo/Packages`，说明多套了一层目录，需要把里面的内容移到仓库根）。
- 仓库是 private 且账号是免费版：免费账号不能给私有仓库开 Pages（见第 6 条）。
- 分支名不是 `main`（比如还是 `master`）：Pages 里就选实际分支，或把本地分支改成 `main` 再推。
- 刚推送完：等 1–2 分钟、强制刷新页面；必要时到仓库 Actions / Settings→Pages 看有没有报错。

### 2. 落地页能打开，但 Sileo 刷新不到包 / 源是空的

- 源地址要填**根地址** `https://zyh-repo.github.io/zyh-repo/`，不要填 `.../Packages`、`.../index.html`。
- 打开 `https://zyh-repo.github.io/zyh-repo/Packages` 确认里面有 `Package:` 段落；空文件或 404 就回看第 1 条。
- `debs/` 里放了 deb 但没重建索引：跑 `./tools/publish.sh` 或 `./tools/build.sh` 并推送。
- 架构不匹配：Sileo 只显示与当前设备架构一致的包（例如你的设备是 rootless，就不会显示 `iphoneos-arm` 的 stanza）。确认 `Packages` 里确实有对你设备架构的 stanza。
- 客户端缓存：Sileo 源页面下拉刷新；还不行就删除源再重新添加。
- `BASE_URL` 为空只影响图标 / 横幅，**不会**让包消失，可以排除。

### 3. Sileo 里包没有图标 / 首页没有精选横幅

这是 `BASE_URL` 为空时的正常表现：

- 索引里不会写 `Icon` / `Depiction`，所以 Sileo 列表里的包没有图标、也没有「描述页」入口；
- 精选横幅默认是关闭的：`repo.conf` 的 `FEATURED_PACKAGE` 留空就不会生成 `sileo-featured.json`；需要横幅时填上包名并重建；
- 落地页会用 `assets/package-icon-default.png` 兜底，但 Sileo 用的是索引字段，兜底无效。

修复：`./tools/set-url.sh "https://zyh-repo.github.io/zyh-repo"` → 重新推送 → 等 Pages 生效 → Sileo 删源重加或清缓存刷新。另外确认 `icons/<Package>.png`、`assets/banner.png` 都存在（`verify.sh` 会检查）。

### 4. 加了新 deb，Sileo 里看不到新版本

- 只把 deb 拷进 `debs/` 不算发布，必须重新构建索引：`./tools/publish.sh path/to/pkg.deb`（或 `./tools/build.sh`）。
- 用 App 的 `deb_publish` 发布过的话，再补跑一次 `./tools/build.sh`（补字段、重建压缩索引和 Release）。
- 确认已 `git push`，并等 GitHub Pages 更新（1–2 分钟），再在 Sileo 里下拉刷新。
- 同包同架构有多个版本时只索引最高版本，旧 deb 不进索引是正常的；版本号没变大（比如还是 `1.0`）也会看起来「没更新」。
- 别忘了很多客户端会缓存：Sileo 源页面下拉刷新，必要时删源重加。

### 5. 架构怎么选 / 要发几个 deb

一个插件发三个架构最省心（同一个 `Package` 名，`Architecture` 不同，文件名带架构后缀）：

| Architecture 字段 | 叫法 | 面向 |
| --- | --- | --- |
| `iphoneos-arm` | rootful（有根） | 传统有根越狱环境 |
| `iphoneos-arm64` | rootless（无根） | Dopamine 等无根越狱环境 |
| `iphoneos-arm64e` | roothide | roothide Bootstrap 环境 |

Sileo 会自动只显示匹配设备架构的包，用户不需要手动选；三种都发，任何设备都能装。打包时架构写对（`dpkg-deb -b` 前先确认 control 里的 `Architecture`），发布后跑 `./tools/verify.sh` 校验。

### 6. 能不能用私有仓库

- **GitHub Pages 免费账号只支持 public 仓库**。想让源地址不公开，需要 GitHub Pro 之类的付费计划开通「私有仓库 Pages」。
- 本项目的源里 deb 本身就是公开可下载的，私有仓库只隐藏「索引/页面」，不能保护 deb 内容，也没有访问控制能力，不要往这里放敏感内容。
- 不想用 GitHub Pages 的话，Cloudflare Pages / Netlify / 自己的服务器都可以，流程和「替代托管」一节相同。

### 7. 落地页用 `file://` 打开看不到包

`index.html` 通过 `fetch('Packages')` 读索引，浏览器的 `file://` 协议会拦截这个请求，属于正常现象。本地预览请用 `./tools/serve.sh`，线上用 `http(s)://` 地址访问。

### 8. 中文乱码 / 文件校验失败

- 所有文本文件统一 **UTF-8（无 BOM）+ LF 换行**；`Packages` 的 `Description` 允许中文，但续行必须以一个空格开头。
- 用编辑器保存时关掉「CRLF」「带 BOM」选项；Windows 桌面 git 建议设置 `core.autocrlf false`。
- 发布前跑 `./tools/verify.sh`，失败项会直接指出来。

### 9. 脚本能在别的目录调用吗

可以。`tools/` 下所有脚本都会自己定位仓库根（`$(cd "$(dirname "$0")/.." && pwd)`），在任意工作目录用相对 / 绝对路径调用都行；`build.py` 只用 Python 标准库，脚本会自动探测 Python 3。
如果环境缺少 `/bin/bash` / `/usr/bin/env`（`./tools/xxx.sh` 报 `bad interpreter`），用 `bash tools/xxx.sh` 调用即可；Windows 上用 Git Bash / WSL 运行同样的命令。

### 10. 源有签名吗 / 会被收费吗

flat 源，无签名（不收钱、纯自用），Sileo 添加时不会要求验证。也因为没有签名，**不要往源里放来历不明的 deb**，本仓库只放自己打的包。

---

## 维护红线

- 产物里不出现作者个人标签、本机绝对路径；文件全部 UTF-8 + LF。
- `Packages` / `Release` / `sileo-featured.json` 是生成物，不要手改；改配置走 `repo.conf` + `./tools/build.sh`。
- 提交前：`./tools/build.sh` → `./tools/verify.sh` → 本地 `./tools/serve.sh` 看一眼 → 再推送。
- 新增脚本 / 改脚本参数时，同步更新本文件和 `使用说明.md` 里的命令示例。

---

给最终用户的加源、装包、卸载与常见问题说明见 [`使用说明.md`](使用说明.md)。
