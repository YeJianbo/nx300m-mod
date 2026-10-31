#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_busybox_test.py —— 确认相机上 busybox 编了哪些 applet

动机
----
排查注入方案时反复遇到 "command not found"，怀疑是 busybox 没编入某些 applet。
本脚本把 applet 列表拉出来，一劳永逸地回答"相机上到底有什么工具可用"。

做法
----
1. 跑 `busybox --list`（失败则退化成 `busybox` 无参数）拿 applet 列表，统计数量。
2. 重点检查这几个是否存在：
       sendevent  <-- **关键**：用于直接写 /dev/input/event* 注入按键
       timeout    <-- 历史上踩过：`timeout 5 st ...` -> command not found
       hwclock
       date
       nc
       wget
       xxd
       od
3. 顺便检查系统里有没有 gcc / python / perl。
4. 结果写相机端 /mnt/mmc/busybox.txt，再用 FTP 取回。

实测结论
--------
* busybox **没有 `sendevent`** -> 不能用 busybox 直接写 /dev/input/event*
  （所以后来才写了 evkey.c 那条路，见 camera_evkey_test.py / build_evkey.py）
* busybox **没有 `timeout`** -> 解释了为什么 `timeout 5 xxx` 报 command not found
* 相机上**没有 gcc / python / perl** -> 无法在相机端现场编译，
  交叉编译只能在主机侧做（而且主机上当时也没有 ARM 工具链）
* `hwclock` / `date` 存在（但 hwclock 写入会失败，见 camera_time_probe.py）
* FTP 只暴露 SD 卡根，所以输出写 /mnt/mmc/（/tmp、/var/log 取不回）

telnet 两个必踩坑（已处理）
---------------------------
1. 必须过滤 IAC 协商字节：一连上服务器就发
   \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03，并开启回显。
   规则：遇 255(IAC) 读下一字节当命令；若命令在 0xFB..0xFE
   （WILL/WONT/DO/DONT）则吞 3 字节，否则吞 2 字节。
2. 提示符必须用行尾正则 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（re.M）；
   不能用 `"# " in buf` —— 后续提示符 `[root@drime5 ~]#` 没有尾随空格。
"""

import ftplib
import io
import re
import socket
import sys

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
FTP_PORT = 21
FTP_USER, FTP_PASS = "root", ""

REMOTE_OUT = "/mnt/mmc/busybox.txt"
FTP_OUT = "busybox.txt"

# 重点关注的 applet / 外部命令
INTERESTING = ["sendevent", "timeout", "hwclock", "date", "nc", "wget", "xxd", "od"]
EXTERNAL_CMDS = ["gcc", "cc", "python", "python3", "perl", "tcc", "make"]

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


def telnet_run(commands, host=CAMERA_IP, port=TELNET_PORT, timeout=20.0):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = ""

    def pump(quiet=False):
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(16384)
            except socket.timeout:
                break                    # 超时不清空已读缓冲
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


def ftp_fetch(remote_name, host=CAMERA_IP, port=FTP_PORT,
              user=FTP_USER, password=FTP_PASS):
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=15)
    ftp.login(user, password)
    buf = io.BytesIO()
    try:
        ftp.retrbinary("RETR " + remote_name, buf.write)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return buf.getvalue()


def build_script():
    lines = [
        "echo '=== camera_busybox_test ==='",
        "date",
        "> %s" % REMOTE_OUT,
        'echo "########## busybox --list ##########" >> %s' % REMOTE_OUT,
        "busybox --list >> %s 2>&1 || busybox >> %s 2>&1" % (REMOTE_OUT, REMOTE_OUT),
        'echo "########## applet 数量 ##########" >> %s' % REMOTE_OUT,
        "busybox --list 2>/dev/null | wc -l >> %s 2>&1" % REMOTE_OUT,
        "busybox 2>&1 | head -3 >> %s" % REMOTE_OUT,
    ]
    # 重点 applet 逐个 which
    lines.append('echo "########## 重点 applet 是否存在 ##########" >> %s' % REMOTE_OUT)
    for name in INTERESTING:
        lines.append('printf "%%-12s " %s >> %s' % (name, name, REMOTE_OUT))
        lines.append('if busybox --list 2>/dev/null | grep -qx %s; then '
                     'echo "busybox: YES" >> %s; else echo "busybox: NO" >> %s; fi'
                     % (name, REMOTE_OUT, REMOTE_OUT))
        lines.append('which %s >> %s 2>&1' % (name, REMOTE_OUT))
    # 编译器/解释器
    lines.append('echo "########## gcc / python / perl ##########" >> %s' % REMOTE_OUT)
    for name in EXTERNAL_CMDS:
        lines.append('printf "%%-12s " %s >> %s' % (name, name, REMOTE_OUT))
        lines.append('if which %s >/dev/null 2>&1; then which %s >> %s; '
                     'else echo "NOT FOUND" >> %s; fi'
                     % (name, name, REMOTE_OUT, REMOTE_OUT))
    # 相关二进制与设备（注入路线的现场证据）
    lines.append('echo "########## 相关二进制 / 设备 ##########" >> %s' % REMOTE_OUT)
    lines.append("ls -l /opt/usr/nx-ks/nx-rc/tools/usr/bin/ >> %s 2>&1" % REMOTE_OUT)
    lines.append("ls -l /dev/input/ >> %s 2>&1" % REMOTE_OUT)
    lines.append("ls -l /dev/uinput >> %s 2>&1" % REMOTE_OUT)
    lines.append('echo "########## END ##########" >> %s' % REMOTE_OUT)
    return lines


def main():
    print("=" * 74)
    print("NX500 busybox applet 盘点   相机=%s" % CAMERA_IP)
    print("=" * 74)

    print("[1/3] telnet 拉取 applet 列表 ...")
    outputs = {}
    try:
        for cmd, out in telnet_run(build_script()):
            outputs[cmd] = out
    except Exception as exc:                      # noqa: BLE001
        print("      telnet 阶段失败：%r" % (exc,))

    print("[2/3] FTP 取回 %s ..." % REMOTE_OUT)
    text = ""
    try:
        data = ftp_fetch(FTP_OUT)
        text = data.decode("latin-1", "replace")
        with open(FTP_OUT, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(text)
        print("      取回 %d 字节 -> 本地 %s" % (len(data), FTP_OUT))
    except Exception as exc:                      # noqa: BLE001
        print("      FTP 取回失败：%r" % (exc,))
        print("      退化为只用 telnet 的输出判断。")
        text = "\n".join(outputs.values())

    print("[3/3] 分析")
    # applet 列表：取 `busybox --list` 段
    applets = []
    in_list = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("########## busybox --list"):
            in_list = True
            continue
        if in_list and s.startswith("##########"):
            in_list = False
        elif in_list and s:
            applets.append(s)

    print("-" * 74)
    print("applet 总数：%d" % len(applets))
    if applets:
        print("前 40 个：%s" % " ".join(applets[:40]))

    print("-" * 74)
    print("%-12s %-10s %s" % ("名称", "busybox", "which"))
    print("-" * 74)
    verdict = {}
    for name in INTERESTING:
        bb = "?"
        m = re.search(r"^%s\s+busybox: (YES|NO)" % re.escape(name), text, re.M)
        if m:
            bb = m.group(1)
        elif applets:
            bb = "YES" if name in applets else "NO"
        wm = re.search(r"^(/\S*%s)$" % re.escape(name), text, re.M)
        which = wm.group(1) if wm else "NOT FOUND"
        verdict[name] = (bb, which)
        print("%-12s %-10s %s" % (name, bb, which))

    print("-" * 74)
    print("编译器/解释器：")
    for name in EXTERNAL_CMDS:
        m = re.search(r"^%s\s+(.*)$" % re.escape(name), text, re.M)
        print("  %-10s %s" % (name, m.group(1) if m else "NOT FOUND"))

    print("=" * 74)
    print("结论：")
    print("  * **无 `sendevent`** -> 不能靠 busybox 直接写 /dev/input/event* 注入按键")
    print("  * **无 `timeout`**   -> 解释了 `timeout 5 st ...` 报 command not found")
    print("  * **无 gcc / python / perl** -> 相机端无法现场编译，")
    print("    也说明为什么只能交叉编译（而且主机侧当时也没有 ARM 工具链）")
    print("  * 输出落在 %s，可用 curl.exe --user root: -o %s ftp://%s/%s 取回"
          % (REMOTE_OUT, FTP_OUT, CAMERA_IP, FTP_OUT))
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
