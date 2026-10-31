#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_keys_probe.py —— NX500 输入设备与键码探测

目标
----
搞清楚相机上有哪些输入设备、各自对应哪个 /dev/input/eventX 节点，
以及每个设备**声明支持哪些键码**（kernel capability 位图）。
这是后来做「按键注入」时的基础资料：如果 S1/S2 不在某个设备的
capabilities/key 位图里，往那个设备写事件就毫无意义。

真机事实（实测）
----------------
输入设备名 → event 节点：
    Drime5 GPIO Keyboard    -> event0
    Drime5 ADC  Keyboard    -> event1
    Melfas MMS100s Touchscreen -> event2

NX500 关键键码：
    S1   = 133     （快门半按）
    S2   = 134     （快门全按）
    MOBILE = 233   （Mobile/Wi-Fi 键）
    EV   = 181     （曝光补偿键）
    OK   = 104     （确定键）
    MENU = 135     （菜单键）

S1=133 / S2=134 就应该出现在 GPIO Keyboard(event0) 的 key 位图里
—— 用位图解析可以立刻验证这一点。

输出
----
同时打印到 stdout 并写相机端 /mnt/mmc/keys.txt，再用 FTP 取回本地。
（原因：FTP 只暴露 SD 卡根 /opt/storage/sdcard，/tmp 与 /var/log 取不回；
 /mnt/mmc 就是 SD 卡挂载点，是唯一可靠的"落盘再取回"位置。）

telnet 客户端的两个必踩坑（本脚本已处理，见 decode_telnet / looks_like_prompt）
----------------------------------------------------------------------------
1. 必须过滤 IAC 协商字节：一连上服务器就发
   \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03，并且**开启回显**。
   过滤规则：遇 255(IAC) 读下一字节当命令；若命令在 0xFB..0xFE
   （WILL/WONT/DO/DONT）则吞 3 字节，否则吞 2 字节。
2. 提示符必须用行尾正则 (\\[[^\\]]*\\]\\s*)?#\\s*$ （re.M）。
   **不能用 `"# " in buf`** —— 后续提示符是 `[root@drime5 ~]#`，
   **没有尾随空格**（被内核吞了），只有首个提示符带空格。
   剥提示符前先去 ANSI 颜色码 re.sub(r"\\x1b\\[[0-9;]*[A-Za-z]", "", s)。
   读取要按行缓冲，socket.timeout 不能清空已读缓冲。
"""

import ftplib
import io
import re
import socket
import sys

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
FTP_PORT = 21
TELNET_USER, TELNET_PASS = "root", ""
FTP_USER, FTP_PASS = "root", ""

REMOTE_OUT = "/mnt/mmc/keys.txt"
FTP_OUT = "keys.txt"

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    """行尾正则匹配提示符；不能用子串 `'# ' in buf`。"""
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
    """过滤 IAC 协商后解码。WILL/WONT/DO/DONT 吞 3 字节，其余吞 2 字节。"""
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


def telnet_run(commands, host=CAMERA_IP, port=TELNET_PORT, timeout=12.0):
    """登录 telnet 依次执行命令，返回 [(命令, 输出)]。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = ""

    def pump(until_prompt=True, quiet=False):
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break                       # 超时不清空已读缓冲
            if not chunk:
                break
            buf += decode_telnet(chunk)
            if not quiet and until_prompt and looks_like_prompt(buf):
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


# ---------------------------------------------------------------------------
# 本机侧：解析 capability 位图
# ---------------------------------------------------------------------------
def parse_key_bitmap(hex_str: str):
    """
    把 /sys/class/input/eventX/device/capabilities/key 的十六进制位图
    解析成键码列表。

    内核格式：以空格分隔的若干个 64 位十六进制"字"，**低位字在后**。
    例如 "0 0 0 0 0 0 0 0 0 2000000000000000" 这类。
    第 i 个 bit 对应键码 i；把整个串看成一个低位在右的大整数即可。
    """
    tokens = hex_str.split()
    if not tokens:
        return []
    # 反转字序（内核先打印高位字），拼成一个大整数
    value = 0
    for tok in reversed(tokens):
        value = (value << 64) | int(tok, 16)
    keys = []
    bit = 0
    while value:
        if value & 1:
            keys.append(bit)
        value >>= 1
        bit += 1
    return keys


def key_name(code: int) -> str:
    """给已知的 NX500 键码加个人话名字，方便肉眼核对。"""
    known = {
        133: "S1(半按快门)",
        134: "S2(全按快门)",
        233: "MOBILE",
        181: "EV",
        104: "OK",
        135: "MENU",
    }
    return known.get(code, "")


# ---------------------------------------------------------------------------
# 相机端要执行的探测片段
# ---------------------------------------------------------------------------
def build_script():
    lines = [
        "echo '=== camera_keys_probe ==='",
        "> %s" % REMOTE_OUT,
        # --- 1. /proc/bus/input/devices：设备名 + handlers(eventX) 的权威来源 ---
        'echo "########## /proc/bus/input/devices ##########" >> %s' % REMOTE_OUT,
        "cat /proc/bus/input/devices >> %s 2>&1" % REMOTE_OUT,
        # --- 2. sysfs 里的设备名 ---
        'echo "########## /sys/class/input/event*/device/name ##########" >> %s' % REMOTE_OUT,
        "for f in /sys/class/input/event*/device/name; do "
        'echo "$f: $(cat $f)"; done >> %s 2>&1' % REMOTE_OUT,
        # --- 3. 每个 event 节点的 capability 位图（核心） ---
        'echo "########## capabilities/key (hex bitmap) ##########" >> %s' % REMOTE_OUT,
        "for f in /sys/class/input/event*/device/capabilities/key; do "
        'echo "$f = $(cat $f)"; done >> %s 2>&1' % REMOTE_OUT,
        # 顺带看看有没有 evdev/rel/abs 等其它 capability
        'echo "########## other capabilities ##########" >> %s' % REMOTE_OUT,
        "ls -l /sys/class/input/event*/device/capabilities/ >> %s 2>&1" % REMOTE_OUT,
        # --- 4. 设备节点权限（注入时需要可写） ---
        'echo "########## /dev/input perms ##########" >> %s' % REMOTE_OUT,
        "ls -l /dev/input/ >> %s 2>&1" % REMOTE_OUT,
        "id >> %s 2>&1" % REMOTE_OUT,
        'echo "########## END ##########" >> %s' % REMOTE_OUT,
    ]
    return lines


def parse_devices(text):
    """
    从 /proc/bus/input/devices 里抽出 [(设备名, [handlers...])]。
    文件格式形如：
        I: Bus=0019 Vendor=0000 Product=0000 Version=0000
        N: Name="Drime5 GPIO Keyboard"
        P: Phys=gpio-keys/input0
        H: Handlers=kbd event0
    """
    devices = []
    name = None
    handlers = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("N: Name="):
            name = line[len("N: Name="):].strip().strip('"')
        elif line.startswith("H: Handlers="):
            handlers = line[len("H: Handlers="):].split()
        elif not line:
            if name is not None:
                events = [h for h in handlers if h.startswith("event")]
                devices.append((name, events))
            name, handlers = None, []
    if name is not None:
        events = [h for h in handlers if h.startswith("event")]
        devices.append((name, events))
    return devices


def parse_caps(text):
    """
    解析形如 /sys/.../event0/device/capabilities/key = 0 0 ... 的段落。
    返回 {event 节点名: [键码]}。
    """
    caps = {}
    for line in text.splitlines():
        if "capabilities/key" not in line or "=" not in line:
            continue
        path, _, bitmap = line.partition("=")
        m = re.search(r"(event\d+)", path)
        if not m:
            continue
        caps[m.group(1)] = parse_key_bitmap(bitmap.strip())
    return caps


def main():
    print("[1/3] telnet 探测输入设备与键码 ...")
    try:
        telnet_run(build_script())
    except Exception as exc:                      # noqa: BLE001
        print("      telnet 阶段失败：%r" % (exc,))

    print("[2/3] FTP 取回 %s ..." % REMOTE_OUT)
    try:
        data = ftp_fetch(FTP_OUT)
    except Exception as exc:                      # noqa: BLE001
        print("      FTP 取回失败：%r" % (exc,))
        return 1

    text = data.decode("latin-1", "replace")
    with open(FTP_OUT, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(text)
    print("      取回 %d 字节 -> 本地 %s" % (len(data), FTP_OUT))

    print("[3/3] 对照表（设备名 ↔ event 节点 ↔ 支持的键码）")
    devices = parse_devices(text)
    caps = parse_caps(text)

    print("=" * 74)
    print("%-34s %-8s %s" % ("设备名", "event", "支持的键码"))
    print("-" * 74)
    for name, events in devices:
        ev = events[0] if events else "-"
        keys = caps.get(ev, [])
        print("%-34s %-8s %s" % (name[:34], ev, ("%d 个" % len(keys)) if keys else "(未读到)"))
        if keys:
            # 按 12 个一行折行打印，已知键码带注释
            chunk = []
            for k in keys:
                nm = key_name(k)
                chunk.append(("%d(%s)" % (k, nm)) if nm else str(k))
            for i in range(0, len(chunk), 6):
                print(" " * 44 + "  ".join(chunk[i:i + 6]))

    print("-" * 74)
    print("关键校验：")
    for name, events in devices:
        ev = events[0] if events else None
        if not ev:
            continue
        keys = caps.get(ev, [])
        if 133 in keys or 134 in keys:
            print("  * %s (%s) 的位图里包含 S1=%d / S2=%d -> %s"
                  % (name, ev, 133, 134,
                     "S1" if 133 in keys else "") + ("S2" if 134 in keys else ""))
    print("  （若 GPIO Keyboard=event0 命中 S1/S2，说明它就是快门事件的源头设备）")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
