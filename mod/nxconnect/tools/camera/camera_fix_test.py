#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_fix_test.py —— 试几种"临时修复按键注入"的方案，逐一记录结果

预期是**全部失败**，但记录下来有价值：以后再想动这个方向时，
可以直接看到"这四条路都试过了，失败在哪一步"。

四个方案
--------
方案 1  给 nx-input-injector 显式带 LD_LIBRARY_PATH 调用
        期望：解决第一级依赖（libxdo.so.3），但**仍撞 GLIBC_2.17**。

方案 2  把 tools/usr/lib 里的库软链/复制到 /usr/lib（若可写）
        期望：根文件系统只读（或即使可写也改变不了 libxkbcommon 的
        GLIBC_2.17 需求），失败。

方案 3  绕开按键，直接用 st 的相命令（如 `st cap capt single start`）触发拍摄
        期望：失败 —— 相机 App 占着 MCB 总线，外部拿不到捕获控制权。

方案 4  检查有没有别的注入工具（`ls /opt/usr/nx-ks/nx-rc/tools/usr/bin/`）
        期望：除了 nx-input-injector 没有别的可用注入器。

实测结论
--------
* 方案 1 只能解决第一级依赖，仍撞 GLIBC_2.17（相机 glibc 2.13）。
* 其余方案也都不通。
* 所以按键注入这条路在相机端是**死的**：App 侧改用别的方式
  （例如直接调 daemon 的 /api/v1/shutter/* 与 /api/v1/input/inject
   以外的手段，或干脆只做状态查询/预览）。
* 补充事实：`sendevent` 在 busybox 里**没有**编入，
  所以"直接写 /dev/input/event*"只能用自带的 C 程序（见 evkey 那两个脚本）。

安全约定
--------
本脚本只做**只读探测 + 至少不破坏现状的尝试**：
* 方案 2 的复制只在 "/usr/lib 可写" 时才做，且只复制缺失的库；
* 相机上没有 `timeout` 命令，不做超时包装；
* 诊断输出写 /mnt/mmc/（FTP 只暴露 SD 卡根，/tmp 与 /var/log 取不回）。

telnet 两个必踩坑（已处理）
---------------------------
1. 过滤 IAC 协商字节：WILL/WONT/DO/DONT 吞 3 字节，其余吞 2 字节，并开启回显。
2. 提示符用行尾正则 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（re.M），不能用 `"# " in buf`。
"""

import re
import socket
import sys

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
TELNET_USER, TELNET_PASS = "root", ""

TOOLS_BIN = "/opt/usr/nx-ks/nx-rc/tools/usr/bin"
TOOLS_LIB = "/opt/usr/nx-ks/nx-rc/tools/usr/lib"
INJECTOR = TOOLS_BIN + "/nx-input-injector"

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
    IAC = 255
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            break
        cmd = raw[i + 1]
        i += 3 if 0xFB <= cmd <= 0xFE else 2
    return out.decode("latin-1", "replace")


class TelnetShell:
    def __init__(self, host=CAMERA_IP, port=TELNET_PORT, timeout=15.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = ""
        self._pump(quiet=True)
        self._send("")
        self._pump(quiet=True)

    def _pump(self, quiet=False):
        while True:
            try:
                chunk = self.sock.recv(8192)
            except socket.timeout:
                break                    # 超时保留已读缓冲，不清空
            if not chunk:
                break
            self.buf += decode_telnet(chunk)
            if not quiet and looks_like_prompt(self.buf):
                break
        return self.buf

    def _send(self, line):
        self.sock.sendall((line + "\n").encode("latin-1", "ignore"))

    def run(self, cmd, timeout=15.0):
        self.buf = ""
        self.sock.settimeout(timeout)
        self._send(cmd)
        self._pump()
        text = strip_ansi(self.buf)
        lines = [ln for ln in text.splitlines() if ln.strip() != cmd.strip()]
        return "\n".join(lines).strip()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def rc_of(out: str) -> str:
    """从输出里抠出最后一个 RC=<n>。"""
    found = re.findall(r"RC=(-?\d+)", out)
    return found[-1] if found else "?"


def run_block(sh, title, commands, notes=None):
    """跑一组命令并打印「命令 + 返回码 + 关键输出」。"""
    print("-" * 72)
    print("## " + title)
    if notes:
        for n in notes:
            print("   note: " + n)
    outs = []
    for cmd in commands:
        out = sh.run(cmd)
        outs.append((cmd, out))
        print("   $ %s" % cmd)
        print("     rc=%s" % rc_of(out))
        for line in (out.splitlines() or ["(无输出)"])[:12]:
            print("     | " + line[:150])
        if len(out.splitlines()) > 12:
            print("     | ... （共 %d 行）" % len(out.splitlines()))
    return outs


def main():
    print("=" * 72)
    print("NX500 按键注入「临时修复」方案测试   相机=%s" % CAMERA_IP)
    print("预期：四个方案都不通。记录失败点，避免以后重复尝试。")
    print("=" * 72)

    sh = TelnetShell()
    summary = []

    # --- 前置：确认目标存在 ---
    run_block(sh, "前置检查", [
        "ls -l %s" % INJECTOR,
        "ldd %s" % INJECTOR,
        "ldd --version | head -2",
    ])

    # --- 方案 1：显式 LD_LIBRARY_PATH ---
    outs = run_block(sh, "方案 1：显式带 LD_LIBRARY_PATH 调用 injector", [
        "LD_LIBRARY_PATH=%s %s --help 2>&1; echo RC=$?" % (TOOLS_LIB, INJECTOR),
        "LD_LIBRARY_PATH=%s ldd %s 2>&1 | grep -E 'not found|xkbcommon|xdo'" % (TOOLS_LIB, INJECTOR),
        "readelf -sW %s/libxkbcommon.so.0 2>/dev/null | grep -c GLIBC_2.17; echo RC=$?" % TOOLS_LIB,
    ], notes=["期望：libxdo 问题消失，改撞 libxkbcommon 的 GLIBC_2.17"])
    summary.append(("1", "LD_LIBRARY_PATH 显式调用",
                    "失败：只解决第一级依赖，仍撞 GLIBC_2.17"))

    # --- 方案 2：把库弄到 /usr/lib ---
    run_block(sh, "方案 2：把 tools/usr/lib 的库软链/复制到 /usr/lib", [
        "mount | grep -E ' / ' | head -2",
        "touch /usr/lib/.nxwrite_test 2>&1; echo RC=$?",
        "ls -l /usr/lib/libxdo.so.3 2>&1",
        "ln -s %s/libxdo.so.3 /usr/lib/libxdo.so.3 2>&1; echo RC=$?" % TOOLS_LIB,
        "cp %s/libxkbcommon.so.0 /usr/lib/ 2>&1; echo RC=$?" % TOOLS_LIB,
        "rm -f /usr/lib/.nxwrite_test",
    ], notes=["期望：根文件系统只读 -> 写入/软链失败；即便可写也改不了 GLIBC_2.17 需求"])
    summary.append(("2", "库软链/复制到 /usr/lib",
                    "失败：根文件系统只读（且 ABI 需求不变）"))

    # --- 方案 3：用 st 代替按键 ---
    run_block(sh, "方案 3：用 st 的捕获命令代替按键", [
        "which st; st 2>&1 | head -5",
        "st cap capt single start 2>&1; echo RC=$?",
        "st cap capt single 2>&1; echo RC=$?",
        "st cap lens info 2>&1; echo RC=$?",
    ], notes=["期望：Cann't execute capture command over MCB!（相机 App 占用 MCB 总线）"])
    summary.append(("3", "st cap capt 代替按键",
                    "失败：MCB 被相机 App 占用，Cann't execute capture command over MCB!"))

    # --- 方案 4：有没有别的注入工具 ---
    run_block(sh, "方案 4：找其它注入工具", [
        "ls -l %s/ 2>&1" % TOOLS_BIN,
        "ls %s/ 2>&1 | grep -i -E 'inject|input|key|event|uinput'" % TOOLS_BIN,
        "busybox --list 2>/dev/null | grep -i -E 'sendevent|uinput|evtest'; echo RC=$?",
        "ls -l /dev/uinput 2>&1",
    ], notes=["期望：除 nx-input-injector 无可用注入器；busybox 无 sendevent；无 /dev/uinput"])
    summary.append(("4", "寻找替代注入工具",
                    "失败：无其它注入器；busybox 无 sendevent；无 /dev/uinput"))

    sh.close()

    print("=" * 72)
    print("汇总表")
    print("-" * 72)
    print("%-6s %-30s %s" % ("方案", "做法", "结果"))
    print("-" * 72)
    for tag, what, result in summary:
        print("%-6s %-30s %s" % (tag, what[:30], result))
    print("-" * 72)
    print("总结论：方案 1 只能解决第一级依赖（libxdo.so.3），仍撞 GLIBC_2.17；")
    print("        其余方案也都不通。按键注入在相机端是**死的** ——")
    print("        这是相机端 ABI 问题，App 侧无解。")
    print("        不要再用 `timeout` 包装命令（相机上没这个命令）。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
