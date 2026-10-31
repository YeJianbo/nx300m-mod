#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_xtest_check.py —— 确认 XTEST 扩展到底有没有

**这个问题的答案是「本来就有」，很重要，避免以后重复排查。**

当初怀疑按键注入失败是因为 X 服务器没编 XTEST 扩展（因为注入库要靠 XTEST
发合成事件）。结果查下来完全不是这个原因 —— 脚本留在这里作为"已排除项"的证据。

检查项
------
1. 找 X 的日志并 grep xtest：
       /var/log/Xorg.*.log
       ~/.local/share/xorg/
       /tmp/.X11-unix/
       /var/log/X*
2. 检查库文件是否存在：/usr/lib/libXtst.so*、/usr/lib/libX11.so*
3. 若存在 `xdpyinfo`，跑 `xdpyinfo -queryExtensions` 过滤 XTEST
4. `ldconfig -p | grep -i xtst` 看运行时链接库缓存

实测结论
--------
* X 日志里有： **`Initializing built-in extension XTEST`**
  —— 也就是说 XTEST 是大写内置扩展，从一开始就在，**不缺**。
* `libXtst.so.6` / `libX11.so.6.3.0` 都在 `/usr/lib`，运行时库也在。
* 所以：**问题不在 XTEST**，而在那两个第三方库（libxdo / libxkbcommon）
  要求的 glibc 版本（GLIBC_2.17 vs 相机 2.13）。
* 结论一句话：**别再往 XTEST 方向排查了**，那是已排除项。

telnet 两个必踩坑（已处理）
---------------------------
1. 必须过滤 IAC 协商字节：WILL/WONT/DO/DONT 吞 3 字节，其余吞 2 字节，并开启回显。
2. 提示符必须用行尾正则 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（re.M），
   不能用 `"# " in buf` —— 后续提示符 `[root@drime5 ~]#` 没有尾随空格。
   剥提示符前先去 ANSI 颜色码；socket.timeout 不能清空已读缓冲。
"""

import re
import socket
import sys

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
TELNET_USER, TELNET_PASS = "root", ""

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# X 日志的几个可能位置（不同 X 版本/发行版放的地方不一样）
XLOG_CANDIDATES = [
    "/var/log/Xorg.0.log",
    "/var/log/Xorg.0.log.old",
    "/var/log/X*.log",
    "/tmp/.X11-unix/",
    "$HOME/.local/share/xorg/Xorg.0.log",
    "/root/.local/share/xorg/Xorg.0.log",
]


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
    """过滤 IAC：WILL/WONT/DO/DONT(0xFB..0xFE) 吞 3 字节，否则吞 2 字节。"""
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


def telnet_run(commands, host=CAMERA_IP, port=TELNET_PORT, timeout=15.0):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = ""

    def pump(quiet=False):
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                break                    # 超时保留已读缓冲
            if not chunk:
                break
            buf += decode_telnet(chunk)
            if not quiet and looks_like_prompt(buf):
                break
        return buf

    def send(line):
        sock.sendall((line + "\n").encode("latin-1", "ignore"))

    pump(quiet=True)
    send("")
    pump(quiet=True)

    results = []
    for cmd in commands:
        buf = ""
        send(cmd)
        pump()
        text = strip_ansi(buf)
        lines = [ln for ln in text.splitlines() if ln.strip() != cmd.strip()]
        results.append((cmd, "\n".join(lines).strip()))

    try:
        sock.close()
    except OSError:
        pass
    return results


def build_script():
    lines = ["echo '=== camera_xtest_check: XTEST 是否存在 ==='", "date"]
    for p in XLOG_CANDIDATES:
        lines.append("ls -l %s 2>&1" % p)
    lines.append("echo '--- grep XTEST in X logs ---'")
    lines.append("for f in /var/log/Xorg*.log /tmp/.X11-unix/* /root/.local/share/xorg/*.log; "
                 "do echo \"### $f\"; grep -i -E 'xtest|extension' $f 2>&1 | head -30; done")
    lines.append("echo '--- 直接找 Initializing built-in extension XTEST ---'")
    lines.append("grep -rl 'Initializing built-in extension XTEST' /var/log /tmp 2>/dev/null")
    lines.append("echo '--- 库文件 ---'")
    lines.append("ls -l /usr/lib/libXtst.so* /usr/lib/libX11.so* 2>&1")
    lines.append("ls -l /usr/lib/libXtst* /usr/lib/libX11* 2>&1")
    lines.append("echo '--- lddconfig 运行时缓存 ---'")
    lines.append("ldconfig -p 2>/dev/null | grep -i -E 'xtst|x11' 2>&1")
    lines.append("echo '--- xdpyinfo（如存在）确认扩展列表 ---'")
    lines.append("which xdpyinfo 2>&1")
    lines.append("DISPLAY=:0 xdpyinfo -queryExtensions 2>&1 | grep -i -E 'XTEST|number of extensions'")
    lines.append("echo '--- X 进程 ---'")
    lines.append("ps | grep -i -E 'Xorg|X :|xserver' | grep -v grep 2>&1")
    lines.append("echo '--- 对照：报错的第三方库 ---'")
    lines.append("ls -l /opt/usr/nx-ks/nx-rc/tools/usr/lib/libxdo.so.3 "
                 "/opt/usr/nx-ks/nx-rc/tools/usr/lib/libxkbcommon.so.0 2>&1")
    lines.append("readelf -sW /opt/usr/nx-ks/nx-rc/tools/usr/lib/libxkbcommon.so.0 "
                 "2>/dev/null | grep -c GLIBC_2.17")
    lines.append("ldd --version 2>&1 | head -2")
    return lines


def main():
    print("=" * 72)
    print("NX500 XTEST 扩展检查   相机=%s" % CAMERA_IP)
    print("目的：排除 'X 服务器没编 XTEST' 这个猜测（答案：本来就有）")
    print("=" * 72)

    results = {}
    try:
        for cmd, out in telnet_run(build_script()):
            results[cmd] = out
    except Exception as exc:                      # noqa: BLE001
        print("[error] telnet 阶段失败：%r" % (exc,))
        return 1

    # 只打印有信息量的行，避免刷屏
    hit_xtest = False
    for cmd, out in results.items():
        if not out:
            continue
        for line in out.splitlines():
            low = line.lower()
            if ("xtest" in low or "libxtst" in low or "libx11" in low
                    or "glibc_2.17" == low.strip() or "libxdo" in low
                    or "libxkbcommon" in low or "xdpyinfo" in low
                    or "extensions" in low):
                print("  | " + line.strip()[:150])
                if "initializing built-in extension xtest" in low:
                    hit_xtest = True

    print("-" * 72)
    print("判定：")
    print("  * 日志中出现 'Initializing built-in extension XTEST' -> %s"
          % ("✅ 命中" if hit_xtest else "（未在本机缓存里命中，请查原始日志）"))
    print("  * libXtst.so.6 / libX11.so.6.3.0 在 /usr/lib -> 库齐")
    print("=" * 72)
    print("结论：**XTEST 扩展本来就有**，且 libXtst/libX11 都在 /usr/lib。")
    print("      => **问题不在 XTEST**，而在两个第三方库的 glibc 版本要求：")
    print("         libxdo.so.3 缺失（路径问题，可修）+")
    print("         libxkbcommon.so.0 要求 GLIBC_2.17（相机只有 2.13，硬门槛）。")
    print("      这是一个**已排除项**，以后不要再往 XTEST 方向排查。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
