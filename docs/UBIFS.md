# NX300M 1.15 用户态镜像重建

`tools/deployment_ubifs.py` 直接读取已核验的 `work/stock-1.15/nx300m.bin`，导出 UBIFS 完整 inode 元数据，并复用原有 `rootfs.tar` 的文件内容，在独立 WSL 临时目录生成原样用户态镜像候选。输出位于 `outputs/deployment/ubifs/`。

此流程仅在本地生成 UBIFS/UBI 文件。机身启动、内核挂载和升级器接受行为尚未验证；候选没有进入 SLP 包。

## 直接读出的原厂参数

| 项目 | 原厂值 |
| --- | ---: |
| UBI 在 BIN 中的范围 | `[8010840, 331103320)` |
| PEB | 131072 字节 |
| VID header / data 偏移 | 2048 / 4096 字节 |
| LEB / UBIFS min_io | 126976 / 2048 字节 |
| UBI image sequence / version / erase count | 128692023 / 1 / 0 |
| 卷名 / vol_id / 类型 | rootdir / 0 / dynamic |
| 卷 reserved_pebs / alignment / flags | 2891 / 1 / 1（autoresize） |
| UBIFS 格式 / ro_compat / flags | 4 / 0 / 0 |
| leb_cnt / max_leb_cnt | 2463 / 4096 |
| 默认压缩 | LZO（1） |
| max_bud_bytes / log_lebs / orph_lebs | 8388608 / 5 / 1 |
| fanout / jhead_cnt / lsave_cnt | 8 / 1 / 256 |
| time_gran / reserve pool | 1000000000 ns / 0 字节 |

这些值来自卷表、EC header 和 UBIFS superblock。镜像中的偏移不能单独证明 NAND 的物理 subpage 大小；ubinize 以 2048 的布局参数重现已观察到的 header 偏移。

## 元数据与内容证据

原厂已提交索引共 16831 个 inode：1871 个目录（含根）、13709 个普通文件、1251 个符号链接。全部 inode 均可从根目录到达，没有多路径硬链接组。直接遍历没有发现 XENT、扩展属性 inode、设备节点或其他特殊文件。原厂每个 inode 的 flags 均为 1；数据节点使用 116406 个 LZO 节点和 13602 个不压缩节点。

`stock-metadata.json` 逐 inode 保存：

- 所有路径、mode、uid/gid、nlink、size，以及 atime/mtime/ctime 的秒和纳秒。
- flags、compr_type、creat_sqnum、xattr 计数字段、inline data 和保留字节。
- 符号链接目标、xattr 名称/值及特殊设备号的导出接口；本原厂镜像中后两类为空。
- 从原始 UBIFS 解压读取的普通文件内容 SHA256，以及 inode 在逻辑镜像中的位置。

现有 `rootfs.tar` 的 16830 个成员已逐项与原 UBIFS 对比：普通文件内容、mode、uid/gid、mtime 秒值、目录和链接一致。根 inode 和归档不能表达的时间/UBIFS 字段来自上述清单。没有重新把整个原厂文件系统解包为另一份归档，也没有修改原 TAR 或 BIN。

读取流程同时检查已提交索引中所有索引、inode、目录项及数据节点的 UBIFS CRC；superblock 和 master node 也有 CRC 检查。它依据原厂 master 的提交索引读取，不把未提交 journal 记录当作当前文件系统；原厂 master flags=2，cmt_no=0。

## 运行方式

Windows 使用项目 Conda 环境：

```powershell
python -X utf8 tools/deployment_ubifs.py inspect
python -X utf8 tools/deployment_ubifs.py build
```

`inspect` 重新核对原 BIN 与已有 `source.json` 的 SHA256 身份，读取完整元数据并验证 TAR 内容。`build` 复用清单并重查输入内容，在 Debian 新建 `/tmp/nx300m-mod-ubifs-*` 目录；按原始类型、硬链接关系、属主、权限、时间及 xattr 恢复文件树，再运行 mkfs.ubifs，完成后删除该临时目录。它不使用现有运行根目录或已有命名空间。

mkfs.ubifs 不能从宿主文件树表达原始 ctime、所有纳秒值及每 inode 的 UBIFS 压缩标记。脚本在新镜像上按路径恢复这些固定长度 inode 字段，并重算对应节点 CRC；同时保留原 superblock UUID。不会通过修改 size、nlink 或 xattr 计数字段掩盖结构差异。inode 编号、创建序列号、压缩块划分和索引物理位置允许重新生成；按路径比较文件系统语义。

回读比较检查所有路径、文件内容、链接目标/组、全部语义 inode 字段、xattr 和关键 superblock 参数。只有比较通过，才调用 ubinize 生成独立 `platform-stock-candidate.ubi`；再解析 UBI，检查卷元数据和全部 UBI header，并逐字节对照内嵌 UBIFS 与已检查候选。

## 显式持久覆盖输入

含 mod 的本地候选通过独立参数构建：

```powershell
python -X utf8 tools/deployment_ubifs.py build --overlay outputs/deployment/app-loader/boot-chain/persistent-candidate/rootfs
```

覆盖白名单包含两个已知入口、共用脚本以及 `tools/build_stock_app_loader.py` 的 `PERSISTENT_HOOKS`，不接受任意额外文件。当前为 11 个文件：`usr/bin/hib2.sh`、`opt/share/applications/com.samsung.di-camera-app-nx300.desktop`、`usr/bin/nx300m-camera-launch`，以及 `usr/lib/nx300m-mod/` 下的 `preload.sh` 和七个 SO：`lut_port.so`、`ui_mod.so`、`exposure_controls.so`、`shutter_limit.so`、`color-style.so`、`lcd-capture.so`、`service-loader.so`。构建时读取相邻候选 `report.json` 的 `rootfs_changes`，核对其路径、长度和目标元数据与显式角色一致，再读取当前字节保存在 `mod/input-overlay/`；后续构建和对照使用这份已读取输入。

所有 mod 产物写入 `outputs/deployment/ubifs/mod/`，原样候选保留。`overlay-input-report.json` 记录源路径、实际长度和内容 SHA256，以及相对原厂的精确预期差异；`expected-metadata.json` 保存覆盖后的完整预期路径及元数据。回读按这份完整预期比较，报告所有非预期差异。

预期变化为白名单内 11 个文件、一层新增目录及其父目录的必要目录项元数据：

- `hib2.sh`：改变内容和长度；保留原 `0555`、uid/gid、链接数、时间、flags、压缩和 xattr 字段。
- camera desktop：仅替换内容和长度，保留其原 inode 字段，包括原 `0444`、uid/gid 和时间。
- 新增 `nx300m-camera-launch` 为 `0755`，`preload.sh` 为 `0644`，uid/gid 均为 0，三个时间均取候选声明的 mtime `1403327571`，纳秒为 0。
- 七个新增 SO：`0644`，uid/gid 均为 0，nlink=1，无 xattr；UBIFS flags=1、LZO 标记沿用原厂。
- 新增 `/usr/lib/nx300m-mod`：`0755`，uid/gid 均为 0，nlink=2。SO 未另行声明时间，和新目录一起使用原 `/usr/lib` 的 mtime 秒值 `1418024048`，纳秒为 0。
- `/usr/lib`：目录大小从 93224 增至 93296，nlink 从 43 增至 44；`/usr/bin` 因新增 wrapper 从 38160 增至 38240，nlink 仍为 3；其余字段保留原值。新目录的 UBIFS 大小按实际直接子项计算为 760；desktop 内容替换不改变其父目录大小。新增普通文件数和目录数也由实际新路径计算。

`mod/comparison.json` 的 `expected_stock_differences` 展示上述差异，`unexpected_difference_count` 必须为 0，才能生成 `mod/platform-mod-candidate.ubi`。需要单独回读或重包现有 mod 产物时，可运行 `verify --mod` 或 `wrap --mod`。

含 mod 的实际产物和证据：

| 文件（均在 `ubifs/mod/`） | 用途 |
| --- | --- |
| `input-overlay/`、`overlay-input-report.json` | 实际读取的覆盖文件及来源、模式和预期差异 |
| `expected-metadata.json` | 完整预期文件系统；新增 inode 编号为比较清单内部标识 |
| `mkfs-report.json`、`restored-inode-fields.json` | mkfs 执行及时间等字段恢复明细 |
| `comparison.json` | 对完整预期的回读、原厂预期差异与非预期差异 |
| `rootdir-mod-candidate.ubifs` | 含显式覆盖清单的独立 UBIFS 候选 |
| `ubinize.ini`、`ubinize-report.json` | UBI 卷配置、构建及回读证据 |
| `platform-mod-candidate.ubi` | 包含上述 UBIFS 的独立 UBI 候选 |

最终 11 文件覆盖已完成：hib2 为 2517 字节、desktop 为 122 字节、wrapper 为 282 字节、共享 preload 为 714 字节，七个 SO 与上一轮输入字节完全相同。只执行一次 mkfs；随后按最终目标声明定点将七个 SO 的 mode 归一为 `0644` 并重算对应 inode CRC，没有重跑 mkfs，实际七处变更保存在 `restored-inode-fields.json` 的 `mode_normalization` 中。

最终完整回读得到 16841 个路径（1872 目录、13718 普通文件、1251 符号链接），相对原厂的预期变化为 14 个路径，非预期差异为 0。11 个源文件与输入副本字节相同，候选声明的 mode/uid/gid 与镜像回读一致；原厂 app、其符号链接和 go.sh 保持原值。UBIFS 为 312741888 字节，UBI 为 323092480 字节，卷参数一致且封装载荷逐字节相同。原样 UBIFS 的内容身份保持不变。实际输入、来源、目标元数据及回读结果分别见 `overlay-input-report.json` 与 `comparison.json`。

## 独立工具链

使用 Debian 官方仓库的 `mtd-utils 1:2.3.0-1` 和 `libiniparser4 4.2.6-1`，包文件及解包目录保存在 `toolchain/`。没有进行系统安装或升级。

官方包地址：

- <https://deb.debian.org/debian/pool/main/m/mtd-utils/mtd-utils_2.3.0-1_amd64.deb>
- <https://deb.debian.org/debian/pool/main/i/iniparser/libiniparser4_4.2.6-1_amd64.deb>

如需重新准备工具链，可在 Debian 中执行下列命令，或从上述官方 HTTPS 地址下载对应包后执行 dpkg-deb：

```sh
cd /mnt/d/codex/project/2026-10-19/nx300m-mod/outputs/deployment/ubifs/toolchain
apt-get download mtd-utils=1:2.3.0-1 libiniparser4=4.2.6-1
dpkg-deb -x mtd-utils_2.3.0-1_amd64.deb root
dpkg-deb -x libiniparser4_4.2.6-1_amd64.deb root
```

## 输出与边界

本轮实际执行结果：mkfs.ubifs 与 ubinize 均退出 0。`rootdir-stock-candidate.ubifs` 为 312741888 字节，16831 个路径的内容和语义 inode 字段比较零差异，所有列出的 superblock 参数均相同。mkfs 生成后实际只需恢复 16831 个 inode 的 ctime 秒值，以及 superblock UUID。新 UBIFS 的已提交索引 CRC 检查覆盖 23383 个索引节点、16831 个 inode、16830 个目录项和 130008 个数据节点。

`platform-stock-candidate.ubi` 为 323092480 字节，回读卷元数据一致，UBI header 检查通过，内嵌 UBIFS 与已对照候选逐字节相同。重建过程重新分配 inode 编号、创建序列号及物理布局，因此这些结果不表示整个镜像与原厂字节相同。

| 文件 | 用途 |
| --- | --- |
| `stock-metadata.json` | 原厂来源、卷参数、所有 inode 和内容身份 |
| `mkfs-report.json` | 实际 mkfs 命令、退出码、stdout/stderr |
| `restored-inode-fields.json` | 生成后恢复的 inode 字段明细 |
| `comparison.json` | 新 UBIFS 回读对照结果及差异 |
| `rootdir-stock-candidate.ubifs` | 原样用户态 UBIFS 候选 |
| `ubinize.ini`、`ubinize-report.json` | 卷配置、执行及回读结果 |
| `platform-stock-candidate.ubi` | 独立 UBI 候选 |

原样产物保留原厂内容，显式 overlay 产物按上节的声明清单构建。SLP 外层封装、升级器接受性和机身 snapshot 生命周期仍需后续验证。机身验证前，需先确认内核/升级器对该重建布局的接受条件；已有静态元数据和内容一致性不替代这些证据。
