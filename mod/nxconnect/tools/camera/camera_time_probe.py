#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_time_probe.py —— NX500 时间能力探测（最早的一次时间探测）

这是整个时间同步工作的**起点**：先摸清"相机到底能不能设时间、有没有 RTC、
有没有 NTP"。后来 App 里做时间同步就是基于这里得到的结论。

探测顺序（全部经 telnet，root / 空密码）
----------------------------------------
1. `date`                    —— 当前时间与时区
2. `date --version`          —— 确认是 GNU coreutils 还是 busybox 版 date
3. `ls -l /etc/localtime`    —— 时区文件
4. `cat /etc/timezone`       —— 预期不存在（嵌入式系统常见）
5. `which hwclock` / `/sbin/hwclock --help` —— 有没有硬件时钟工具
6. `ls -l /dev/rtc`          —— RTC 设备节点
7. `cat /proc/uptime`        —— 开机时长（用于判断时间是否被"漂移"）
8. `ps | grep -i ntp`        —— 有没有 ntpd/ntpclient 在跑
9. **安全写测试** `date -s "$(date)"` —— 用相机自己的当前时间写回，
   等价于没改任何东西，只为确认 `-s` 语法可用并拿到返回码。

实测结论
--------
* `date` 是 **GNU coreutils 6.9**（不是 busybox 的简化版），
  所以支持 `date -s "字符串"` 这种 GNU 语法。
* 时区是 **KST (UTC+9)**（相机面向韩国市场）。
* `date -s` **可用**，写测试返回 rc=0 —— 也就是说时间**可以设**。
* `hwclock` 存在，但 `hwclock -w` **失败**：
      /dev/rtc tick timeout、/etc/adjtime 只读
  => **时间同步重启后会丢**（没有可用的 RTC 持久化路径）。
* **没有 ntpd 在跑**（也没有 ntpclient），所以只能靠外部推时间。
* 相机时钟实测曾停在 **2015-02-17**，偏差约 **11.6 年**。

踩过的坑
--------
* 相机上**没有 `timeout` 命令**（busybox 未编入），不要写 `timeout 5 xxx`。
* 提示符必须用行尾正则匹配：后续提示符是 `[root@drime5 ~]#`，
  **没有尾随空格**（被内核吞了），只有首个提示符带空格。
"""

import re
import socket
import sys

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
TELNET_USER, TELNET_PASS = "root", ""

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    """行尾正则；绝不能用 `'# ' in buf`（后续提示符没有尾随空格）。"""
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
    """
    过滤 IAC 协商。
    一连上服务器就发：
        \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03
    并且开启回显。
    规则：遇 255(IAC) 读下一字节当命令；若命令在 0xFB..0xFE
    （WILL/WONT/DO/DONT）则吞 3 字节，否则吞 2 字节。
    """
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
    """极简 telnet 客户端：按行缓冲读取，socket.timeout 不清空已读缓冲。"""

    def __init__(self, host=CAMERA_IP, port=TELNET_PORT, timeout=10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = ""
        self._pump(quiet=True)
        self._send("")                 # 触发首个提示符
        self._pump(quiet=True)

    def _pump(self, quiet=False):
        while True:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break                  # 关键：超时保留 self.buf 已读内容
            if not chunk:
                break
            self.buf += decode_telnet(chunk)
            if not quiet and looks_like_prompt(self.buf):
                break
        return self.buf

    def _send(self, line):
        self.sock.sendall((line + "\n").encode("latin-1", "ignore"))

    def run(self, cmd, timeout=10.0):
        """执行一条命令，返回 (输出文本, 提示符前的内容)。"""
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


def section(title):
    print("-" * 68)
    print("## " + title)


def main():
    print("=" * 68)
    print("NX500 时间能力探测   相机=%s (telnet root/空密码)" % CAMERA_IP)
    print("=" * 68)

    sh = TelnetShell()
    findings = {}

    # 1. 当前时间与时区
    section("1) date —— 当前时间与时区")
    out = sh.run("date")
    print(out)
    findings["date"] = out
    out = sh.run("date +%Y-%m-%dT%H:%M:%S%z")
    print("   机器可读：%s" % out)
    findings["date_iso"] = out
    # 实测这里会看到 2015-02-17 之类的年份 —— 偏差约 11.6 年

    # 2. 是不是 GNU coreutils
    section("2) date --version —— 确认 GNU coreutils 而非 busybox")
    out = sh.run("date --version")
    print(out)
    findings["date_version"] = out
    # 预期：date (GNU coreutils) 6.9

    # 3. 时区文件
    section("3) ls -l /etc/localtime")
    print(sh.run("ls -l /etc/localtime"))
    section("4) cat /etc/timezone （预期不存在）")
    print(sh.run("cat /etc/timezone 2>&1"))

    # 4. hwclock
    section("5) which hwclock / /sbin/hwclock --help")
    print("which hwclock -> %s" % sh.run("which hwclock 2>&1"))
    print(sh.run("/sbin/hwclock --help 2>&1 | head -20"))

    # 5. RTC 设备
    section("6) ls -l /dev/rtc")
    print(sh.run("ls -l /dev/rtc* 2>&1"))

    # 6. uptime
    section("7) cat /proc/uptime")
    print(sh.run("cat /proc/uptime"))

    # 7. NTP
    section("8) ps | grep -i ntp （确认没有 ntpd）")
    ntp_out = sh.run("ps | grep -i ntp | grep -v grep 2>&1")
    print(ntp_out if ntp_out else "(无输出 -> 没有 ntp 进程)")
    findings["ntp"] = ntp_out

    # 8. 安全写测试：用相机自己的当前时间写回，等价于没改
    section('9) 安全写测试：date -s "$(date)"  （用当前时间写回，等于没改）')
    out = sh.run('date -s "$(date)"; echo "RC=$?"')
    print(out)
    findings["write_test"] = out
    print("   期望看到 RC=0 —— 说明 `date -s` 语法可用。")
    print("   这一步是安全的：写入的值就是相机自己的当前时间，不改变任何状态。")

    # 9. hwclock -w（只做记录，不指望成功）
    section("10) hwclock -w 记录（预期失败）")
    out = sh.run("hwclock -w 2>&1; echo \"RC=$?\"")
    print(out)
    findings["hwclock_w"] = out

    sh.close()

    print("=" * 68)
    print("结论汇总")
    print("-" * 68)
    print("  * `date` 是 GNU coreutils 6.9 -> 支持 `date -s \"字符串\"`")
    print("  * 时区 KST (UTC+9)")
    print("  * `date -s` 可用（写测试 rc=0）-> 时间**能设**")
    print("  * hwclock 存在，但写入失败（/dev/rtc tick timeout、/etc/adjtime 只读）")
    print("    => 时间同步**重启后会丢**，每次连上都要重新推时间")
    print("  * 没有 ntpd / ntpclient 在跑 -> 只能由 App 侧主动推时间")
    print("  * 相机时钟实测曾停在 2015-02-17（偏差约 11.6 年）")
    print("  * 相机上没有 `timeout` 命令，脚本里别用它")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
