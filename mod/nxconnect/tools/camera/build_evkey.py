#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_evkey.py —— 本机侧的准备脚本：检查/引导 ARM 交叉编译环境来编译 camera_tools/evkey.c

为什么需要交叉编译
------------------
相机上没有 gcc / python / perl（见 camera_busybox_test.py 的实测结论），
所以 `evkey.c` 只能在主机（Windows/MSYS2 或 WSL）上编译成 ARM 二进制，
再用 FTP 传到相机上跑。

为什么**必须静态链接**（这是本脚本要反复强调的一点）
----------------------------------------------------
相机上是 glibc **2.13**（`ldd --version` 实测）。
主机工具链默认带的 glibc 是 2.17+。
动态链接的话，运行时会报：
    version `GLIBC_2.17' not found
—— 这正是当初 `libxkbcommon.so.0` 撞的那堵墙。
`-static` 把 libc 一起编进二进制，彻底绕开版本问题。

本脚本做什么
------------
1. 用 shutil.which 依次找：arm-linux-gnueabihf-gcc、arm-none-linux-gnueabi-gcc、
   aarch64-linux-gnu-gcc
2. 都没找到 -> 打印清晰的安装指引（MSYS2 的 mingw-w64-x86_64-arm-none-eabi-gcc、
   或 WSL 里 apt install gcc-arm-linux-gnueabihf），并说明为什么静态链接。
3. 找到 -> 执行编译： <cc> -static -O2 -o evkey evkey.c，打印结果与 `file evkey` 输出。
4. 编译成功 -> 打印下一步：用 FTP 上传到相机 /mnt/mmc/ 再执行
   （注意 SD 卡可能 noexec，需要先 cp 到 /tmp 并 chmod +x）。

用法
----
    python build_evkey.py                      # 默认在 ./camera_tools/evkey.c
    python build_evkey.py --source D:\\path\\evkey.c --outdir .
    python build_evkey.py --cc arm-linux-gnueabihf-gcc
"""

import argparse
import os
import shutil
import subprocess
import sys

CAMERA_IP = "192.168.5.39"

# 候选交叉编译器，按优先级排列
CANDIDATE_CCS = [
    "arm-linux-gnueabihf-gcc",       # Debian/Ubuntu 交叉工具链（推荐）
    "arm-none-linux-gnueabi-gcc",    # CodeSourcery / 老版本
    "aarch64-linux-gnu-gcc",         # 64 位 ARM（NX500 是 32 位，仅作兜底）
]

DEFAULT_SOURCES = [
    os.path.join("camera_tools", "evkey.c"),
    "evkey.c",
]

INSTALL_HINT = """
======================================================================
未找到任何 ARM 交叉编译器。请按下面任一种方式安装后重跑本脚本：
======================================================================

【方式 A】WSL / Linux（最省事，推荐）
    sudo apt update
    sudo apt install -y gcc-arm-linux-gnueabihf
    # 安装后二进制在 /usr/bin/arm-linux-gnueabihf-gcc
    # 在 WSL 里编译，产物在 Windows 侧也能通过 /mnt/c/... 直接拿到

【方式 B】MSYS2（Windows 原生，无需 WSL）
    pacman -S mingw-w64-x86_64-arm-none-eabi-gcc
    # 注意：这是 bare-metal 的 newlib 工具链，没有 glibc，
    #      正好配合 -static 使用，产物是纯静态 ARM ELF。

【方式 C】ARM 官方 GNU Toolchain
    下载 arm-gnu-toolchain-*-mingw-w64-i686-arm-none-eabi.zip
    （或 arm-linux-gnueabihf 版本），解压后把 bin/ 加进 PATH。

======================================================================
为什么必须加 -static？（重点）
======================================================================
相机的 glibc 是 **2.13**（`ldd --version` 实测），
而现代工具链自带 glibc 2.17+。动态链接的产物一跑就会报：
    version `GLIBC_2.17' not found
这正是 `libxkbcommon.so.0` 当初撞上的同一堵墙，属于**编译期 ABI 门槛，
配置无法绕过**。`-static` 把 libc 静态编进去，这个版本依赖就不存在了。

（补充：XTEST 扩展本来就有，libXtst.so.6 / libX11.so.6.3.0 也在 /usr/lib，
所以 evkey 这条路唯一要解决的就是"编译出一个静态 ARM 二进制"。）
"""


def find_compiler(explicit=None):
    """按优先级找交叉编译器；explicit 优先。返回 (名字, 绝对路径) 或 (None, None)。"""
    if explicit:
        path = shutil.which(explicit)
        return (explicit, path) if path else (explicit, None)
    for cc in CANDIDATE_CCS:
        path = shutil.which(cc)
        if path:
            return cc, path
    return None, None


def find_source(explicit=None):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for cand in DEFAULT_SOURCES:
        if os.path.isfile(cand):
            return cand
    return None


def toolchain_version(cc_path):
    """打印编译器版本（顺便确认工具链能跑）。"""
    for args in ([cc_path, "--version"],):
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
            first = (proc.stdout or proc.stderr or "").splitlines()
            return first[0] if first else "(无版本输出)"
        except Exception as exc:                   # noqa: BLE001
            return "(无法执行: %r)" % (exc,)
    return "(未知)"


def file_type(path):
    """用 `file` 命令看产物类型；Windows 上没有 file 就退化为读 ELF magic。"""
    if shutil.which("file"):
        try:
            proc = subprocess.run(["file", path], capture_output=True, text=True, timeout=30)
            return (proc.stdout or proc.stderr).strip()
        except Exception as exc:                   # noqa: BLE001
            return "(file 执行失败: %r)" % (exc,)

    # 退化方案：ELF magic + 机器类型（EM_ARM=40, EM_AARCH64=183）
    try:
        with open(path, "rb") as fh:
            head = fh.read(20)
        if head[:4] != b"\x7fELF":
            return "不是 ELF（前 4 字节 %r）" % head[:4]
        bits = "64-bit" if head[4] == 2 else "32-bit"
        machine = head[18] | (head[19] << 8)
        name = {40: "ARM", 183: "AArch64"}.get(machine, "machine=%d" % machine)
        return "ELF %s, %s （未装 file 命令，由 magic 推断）" % (bits, name)
    except Exception as exc:                       # noqa: BLE001
        return "(无法读取产物: %r)" % (exc,)


def is_static_binary(path):
    """
    粗略判断是否静态链接：ELF 里若没有 .interp 段就是静态的。
    没有 readelf 时退化为"在文件里搜 ld-linux 字符串"。
    """
    if shutil.which("readelf"):
        try:
            proc = subprocess.run(["readelf", "-l", path], capture_output=True,
                                  text=True, timeout=30)
            return "INTERP" not in (proc.stdout or "")
        except Exception:                          # noqa: BLE001
            pass
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
        return b"ld-linux" not in blob
    except Exception:                              # noqa: BLE001
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="检查/引导 ARM 交叉编译环境并编译 evkey.c（必须 -static）")
    ap.add_argument("--source", help="evkey.c 路径，默认找 ./camera_tools/evkey.c 或 ./evkey.c")
    ap.add_argument("--outdir", default=".", help="输出目录，默认当前目录")
    ap.add_argument("--cc", help="显式指定交叉编译器")
    ap.add_argument("--keep-going", action="store_true",
                    help="即使找不到编译器也只警告不返回非零码")
    args = ap.parse_args(argv)

    print("=" * 70)
    print("build_evkey —— 为 NX500 编译静态 ARM evkey")
    print("目标相机：%s（glibc 2.13，无 gcc/python/perl）" % CAMERA_IP)
    print("=" * 70)

    # --- 1. 找编译器 ---
    cc_name, cc_path = find_compiler(args.cc)
    print("[1/5] 探测 ARM 交叉编译器")
    for cand in CANDIDATE_CCS:
        found = shutil.which(cand)
        print("      %-30s %s" % (cand, found if found else "未找到"))

    if not cc_path:
        print()
        print(INSTALL_HINT)
        print("=" * 70)
        print("结论：本机**没有** ARM 交叉编译器，无法编译 evkey.c。")
        print("      这正是当初 evkey 路线没走通的原因（不是思路不行，是工具链缺）。")
        print("      装好上面任一工具链后重跑本脚本即可。")
        print("=" * 70)
        return 0 if args.keep_going else 1

    print("      -> 使用：%s (%s)" % (cc_name, cc_path))
    print("      -> 版本：%s" % toolchain_version(cc_path))

    # --- 2. 找源码 ---
    print("[2/5] 定位 evkey.c")
    source = find_source(args.source)
    if not source:
        print("      未找到 evkey.c（找过：%s）" % ", ".join(DEFAULT_SOURCES))
        print("      提示：源码属于 camera_tools/ 目录；")
        print("           若只重建了脚本而没恢复 C 源码，用 --source 指定实际路径。")
        return 1
    print("      -> %s (%d 字节)" % (source, os.path.getsize(source)))

    # --- 3. 编译 ---
    out_path = os.path.join(args.outdir, "evkey")
    cmd = [cc_path, "-static", "-O2", "-o", out_path, source]
    print("[3/5] 编译（**-static 必须**：相机 glibc 2.13，动态链接会撞 GLIBC_2.17）")
    print("      $ " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as exc:                       # noqa: BLE001
        print("      编译调用失败：%r" % (exc,))
        return 1

    if proc.stdout:
        print("      --- stdout ---")
        for line in proc.stdout.splitlines()[:40]:
            print("      | " + line)
    if proc.stderr:
        print("      --- stderr ---")
        for line in proc.stderr.splitlines()[:40]:
            print("      | " + line)
    print("      rc=%d" % proc.returncode)

    if proc.returncode != 0 or not os.path.isfile(out_path):
        print("      编译失败。常见原因：")
        print("        * 源码里 include 了相机专有头文件（evkey.c 应只用 linux/input.h 等标准头）")
        print("        * 用了 glibc 专有接口而工具链是 newlib（改用裸 write() 即可）")
        print("        * 忘了 -static 之外的 -march/-mfloat-abi（默认值一般就对）")
        return 1

    # --- 4. 验证产物 ---
    size = os.path.getsize(out_path)
    print("[4/5] 产物检查")
    print("      -> %s (%d 字节)" % (out_path, size))
    print("      -> file: %s" % file_type(out_path))
    static_ok = is_static_binary(out_path)
    print("      -> 静态链接：%s" % ("✅ 是" if static_ok else "❌ 否（务必加 -static 重编！）"))
    if not static_ok:
        print("         动态链接的产物在相机上会报 `GLIBC_2.17' not found，别用。")

    # --- 5. 下一步 ---
    print("[5/5] 下一步：上传到相机并执行")
    print("      1) 上传（FTP 用户 root，**密码为空**，`root:` 的冒号不能省）：")
    print("         curl.exe --user root: -T %s ftp://%s/evkey" % (out_path, CAMERA_IP))
    print("      2) **SD 卡可能挂载为 noexec**，所以先拷到 /tmp 再加执行位：")
    print("         cp /mnt/mmc/evkey /tmp/evkey && chmod +x /tmp/evkey")
    print("      3) 注入测试（键码：S1=133、S2=134、EV=181、OK=104，设备 event0）：")
    print("         /tmp/evkey 0 133 down; sleep 0.45; /tmp/evkey 0 134; \\")
    print("             sleep 1; /tmp/evkey 0 133 up")
    print("         （这就是 App 里唯一可行的快门序列 A：半按保持 -> 全按 -> 松半按）")
    print("      4) 验证：用 8080 CGI 的 /cgi-bin/dirlist?p=/DCIM/100PHOTO")
    print("         看有没有多出新的 **SAM 编号**（去掉扩展名后去重）。")
    print("         注意一次快门写 JPG+SRW 两个文件，**不能数文件个数**。")
    print("      5) 相机上没有 `timeout` 命令，别用它包命令。")
    print("=" * 70)
    print("完成：%s（%s）" % (out_path, "静态链接 OK" if static_ok else "需要重编"))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
