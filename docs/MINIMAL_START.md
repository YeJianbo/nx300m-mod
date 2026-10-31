# NX300M 1.15 最小启动候选

本候选用于离线检查从原厂 `start_script` 调用 SD 诊断脚本的最小改动。它不是完整 Mod 安装包；首次实机验证仅检查诊断标记，不启动任何功能模块。

2026-10-24已有MFW的 `autoexec.sh` 入口已通过 [实机诊断](MFW_ENTRY.md)，确认root执行及完成标记，不为此刷入本候选。本目录的离线产物与验证记录保留，不代表用户此前刷入的就是本候选。

## 改动范围

- 输入是 `work/stock-1.15/source.json` 核验的 NX300M 原厂 1.15 固件。
- rootfs 仅替换 `/usr/lib/libmisc.so`：从原厂 7340 字节库生成 7459 字节库，保留原来的动态符号和其他原字节，只改变 `start_script` 入口分支、RX 段长度和追加的调用代码。
- 保持该文件原来的 mode、uid、gid 和时间元数据；全树回读检查内容、语义元数据、硬链接和 xattr。
- SLP 只替换 `platform.img`。`vImage`、`D4_IPL.bin`、`D4_PNLBL.bin`、`uImage` 和 `pcache.list` 逐字节保持原样；保留原厂版本、日期。
- 不加入 BusyBox 替换、旧 MFW 文件、曝光补丁、其他 preload 库或持久服务。

这里的“最小”指内容改动范围。外层 SLP 仍是完整固件包，原厂升级流程可能重写多个分区；其他分区内容不变，不代表升级时不会写入这些分区，也不代表只在相机上写入库中的几个字节。

入口命令为：

```sh
test -f /mnt/mmc/nx300m-mod/start.sh && /bin/sh /mnt/mmc/nx300m-mod/start.sh >/dev/null 2>&1 &
```

生成的 SD `start.sh` 只写 `minimal-start.log` 和 `minimal-start.ok`，记录日期、身份、内核信息。存在 `DISABLED` 或已有成功标记时直接退出。它不拍照、不联网、不调用其他脚本、不操作 snapshot，也不写相机内部文件系统。

**此路径也被完整 Mod 服务包使用。必须使用独立、干净的 SD staging／目录，不能将诊断包合并进已有 `nx300m-mod` 目录。否则固件入口可能执行已有的完整 `start.sh`。** 构建器不会读取或写入实体 SD 卡。

## 本地构建与产物

在项目根目录使用 Conda Python：

```powershell
python -X utf8 tools/build_minimal_start_candidate.py
```

只生成并核对库、overlay 和 SD 探针可加 `--prepare-only`。完整构建加 `--check-stock-parser`，会在封装后调用已有的只读 ARM 隔离环境，运行原厂 SLP reader 和 CRC 代码检查本次候选，不执行升级主程序。此前已通过的原厂、错误型号和坏 CRC 对照不重复运行。

完整构建复用 WSL Debian 的 `mkfs.ubifs`、`ubinize` 和现有回读工具，输出始终位于独立目录 `outputs/deployment/minimal-start/`：

| 路径 | 内容 |
| --- | --- |
| `report.json` | 本次实际构建状态、修改范围和未验证项 |
| `start-hook/build-report.json` | 原厂库与生成库的字节检查 |
| `overlay/rootfs/usr/lib/libmisc.so` | 唯一的 rootfs overlay 文件 |
| `sd/nx300m-mod/start.sh` | 诊断脚本，不能与完整服务包混用 |
| `nx300m-minimal-start-sd.tar.gz` | 只含上述一个脚本的 SD 准备归档 |
| `ubifs/comparison.json` | 全树内容与语义元数据回读 |
| `ubifs/ubinize-report.json` | UBI header、volume 与 UBIFS payload 回读 |
| `firmware/nx300m-minimal-start-1.15.slp` | 本地开发候选，不自动改名或复制至卡 |
| `firmware/build-report.json` | SLP 长度、偏移、CRC、EOF 和原分区保持检查 |
| `parser/report.json` | 可选的本次候选原厂解析器与 CRC 实际执行结果 |

脚本先独立从原 TAR 重建期望 patch，核对生成器的实际字节及报告，再允许进入打包。全树相对原厂应只有 `usr/lib/libmisc.so` 的 `size`、`sha256` 两项变化；路径集合和其他语义元数据不变。UBIFS 的物理节点布局与 inode 编号可能因重建变化。

## 本次离线结果（2026-10-24）

- UBIFS：312,741,888 字节，16,831 个路径回读通过，非预期差异为 0。
- UBI：323,092,480 字节，header／volume 校验通过，封装 payload 与上述 UBIFS 逐字节相同。
- SLP：331,140,200 字节，偏移、长度、CRC、完整 EOF 和五个未变分区检查通过。
- 原厂 SLP parser：本候选返回 0，确实执行到原厂 reader、CRC stream 和 CRC update；隔离执行耗时 42.919 秒。未执行原厂升级主程序、flash writer 或相机命令。
- 两个改动工具的 Python 编译通过。未重复已有 start-hook fixture 和解析器旧对照。

原厂解析器本身不完整核验声明偏移及最终 EOF，这两项由独立 SLP 构建器回读校验覆盖。以上均为离线结果。

## 验证边界与以后实机验收

离线构建和解析检查不能证明真实内核能挂载、相机一定调用此入口、升级器接受同版本包或升级后可恢复。这些状态保留在报告中；本轮不执行升级器，不访问实体相机或 SD 卡。拔卡只会使入口找不到 SD 脚本，不会撤销已经写入固件的库修改。

后续若进入单独授权的实机阶段，先使用只有诊断脚本的干净 SD 目录。开机后预期出现 `minimal-start.ok` 和日志，日志包含 `probe complete`；原厂拍摄功能仍应正常。没有标记只能说明这条触发链未获确认，应保留日志调查，不能据此加载更多模块。无脚本／禁用状态和休眠恢复也仍需分别实机确认。本说明不构成已经验证的安装或救砖流程。
