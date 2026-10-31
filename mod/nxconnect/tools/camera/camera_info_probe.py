#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_info_probe.py —— 探测 NX500 到底能提供哪些信息，决定 App 信息面板能显示什么。

做法（因为相机上没有 timeout 命令、也没有 /tmp 可写）：
    1) 用 telnet 登录（root / 空密码），把一大批候选命令的输出重定向到 /mnt/mmc/info.txt
       —— FTP 的根目录就是 SD 卡 /opt/storage/sdcard，但 FTP 不提供 /tmp 或 /var/log，
          所以诊断输出必须写到 /mnt/mmc/ 才能被取回。
    2) sync 落盘。
    3) 用 curl.exe 通过 FTP 把 info.txt 取回本地并打印（按组截断，避免刷屏）。

已知的部分结论（真机实测，脚本会再确认一遍）：
    - st bat get_soc / st bat get_voltage 都返回 ret=-1（硬件不提供电量百分比）
    - sysfs 只有 /sys/class/power_supply/battery/capacity = 1330（原始 mAh，不是百分比）
      以及 status = Unknown
    - st cap lens info 返回 "Cann't execute capture command over MCB!"
      （相机 App 占用 MCB 总线，镜头信息拿不到）
    - st cap capdtm getusr ADJUSTSHUTTERTYPE 可用，
      返回如 "UserData is ADJUSTSHUTTERTYPE_MECHA (0x750000)"

用法：
    python camera_info_probe.py 192.168.5.39
"""

import os
import re
import socket
import subprocess
import sys
import time

IAC = 255
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

REMOTE_OUT = "/mnt/mmc/info.txt"
LOCAL_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "info.txt")

# 要逐个探测的 capdtm 用户参数（getusr <名字>）
CAPDTM_PARAMS = [
    "ADJUSTSHUTTERTYPE",
    "ADJUSTSHUTTERVALUE",
    "ADJUSTAPERTURETYPE",
    "ADJUSTAPERTUREVALUE",
    "ADJUSTISOVALUE",
    "ADJUSTEVVALUE",
    "ADJUSTWBVALUE",
    "ADJUSTFLASHVALUE",
    "ADJUSTRESOLUTION",
    "ADJUSTQUALITY",
    "ADJUSTDRIVEMODE",
    "ADJUSTMETERING",
    "ADJUSTFOCUSMODE",
    "ADJUSTAFMODE",
    "ADJUSTSTILLIMAGESIZE",
]


def build_command_list():
    """按分组构造命令列表：[(组名, [命令, ...]), ...]"""
    groups = []

    groups.append(("电量 / 电源", [
        "st bat",
        "st bat get_soc",
        "st bat get_voltage",
        "st pmu show",
        "cat /sys/class/power_supply/battery/capacity",
        "cat /sys/class/power_supply/battery/status",
        "cat /sys/class/power_supply/*/capacity",
        "cat /sys/class/power_supply/*/status",
        "ls /sys/class/power_supply/",
    ]))

    groups.append(("镜头", [
        "st cap lens",
        "st cap lens info",
        "st cap lens status",
        "st cap lens getzoom",
    ]))

    groups.append(("拍摄参数（capdtm getusr）", [
        "st cap capdtm getusr",
    ] + ["st cap capdtm getusr %s" % p for p in CAPDTM_PARAMS]))

    groups.append(("机型 / 固件 / 系统", [
        "cat /etc/version.info",
        "uname -a",
        "cat /proc/version",
        "cat /etc/os-release",
        "date '+%Z %z'",
        "date",
        "cat /etc/localtime | wc -c",
        "ls -l /etc/localtime",
        "which date",
        "date --version",
    ]))

    groups.append(("存储 / 文件系统", [
        "df -h /mnt/mmc",
        "df -h",
        "mount",
        "ls -l /mnt/mmc",
    ]))

    groups.append(("st 子命令帮助", [
        "st",
        "st help",
        "st --help",
        "st bat --help",
        "st cap --help",
        "st cap capdtm --help",
        "st pmu --help",
        "st app --help",
    ]))

    return groups


class Telnet(object):
    """这里再写一份精简的 telnet 读取器：IAC 过滤 + 行尾提示符正则。"""

    def __init__(self, ip, port=23, timeout=0.6):
        self.sock = socket.create_connection((ip, port), 5.0)
        self.sock.settimeout(timeout)
        self.buf = ""

    def _pump(self):
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return
        except OSError:
            return
        if not chunk:
            return
        out = bytearray()
        i = 0
        n = len(chunk)
        while i < n:
            b = chunk[i]
            if b == IAC:
                if i + 1 >= n:
                    break
                cmd = chunk[i + 1]
                if 0xFB <= cmd <= 0xFE:
                    i += 3          # WILL/WONT/DO/DONT + 选项
                elif cmd == IAC:
                    out.append(IAC)
                    i += 2
                else:
                    i += 2
                continue
            out.append(b)
            i += 1
        text = ANSI_RE.sub("", out.decode("utf-8", "replace"))
        self.buf += text.replace("\r\n", "\n").replace("\r", "\n")

    def wait(self, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._pump()
            if PROMPT_RE.search(self.buf):
                m = PROMPT_RE.search(self.buf)
                body = self.buf[:m.start()]
                self.buf = self.buf[m.end():]
                return body
            time.sleep(0.02)
        body = self.buf
        self.buf = ""
        return body

    def send(self, s):
        self.sock.sendall(s.encode("utf-8", "replace"))

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def run_all(ip):
    """登录后把命令批量重定向写入 /mnt/mmc/info.txt。"""
    groups = build_command_list()
    tel = Telnet(ip)
    try:
        tel.wait(1.5)                 # banner + IAC
        tel.send("root\n")
        tel.wait(1.0)
        tel.send("\n")
        tel.wait(1.0)                 # 首个提示符

        # 起一个子 shell，把所有输出重定向到文件；用追加方式分步写
        tel.send(": > %s\n" % REMOTE_OUT)
        tel.wait(2.0)

        for group, cmds in groups:
            header = "########## %s ##########" % group
            tel.send("echo '%s' >> %s\n" % (header, REMOTE_OUT))
            time.sleep(0.45)
            tel.wait(1.5)
            for cmd in cmds:
                line = "echo '--- %s' >> %s; %s >> %s 2>&1" % (cmd, REMOTE_OUT, cmd, REMOTE_OUT)
                tel.send(line + "\n")
                # 每条命令之间留 0.45 秒，避免 st 命令互相打断
                time.sleep(0.45)
                tel.wait(1.5)

        tel.send("sync\n")
        time.sleep(1.0)
        tel.wait(3.0)
        tel.send("ls -l %s\n" % REMOTE_OUT)
        print("远端文件状态:")
        print(tel.wait(2.0))
    finally:
        tel.close()


def fetch(ip):
    """用 FTP 把 info.txt 取回本地（curl 的 FTP 语法：--user root: ）。"""
    url = "ftp://%s/info.txt" % ip
    cmd = ["curl.exe", "--user", "root:", "--silent", "--show-error",
           "--max-time", "30", "-o", LOCAL_OUT, url]
    print("取回: %s" % " ".join(cmd))
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        print("找不到 curl.exe，请手动执行：curl.exe --user root: -o info.txt %s" % url)
        return False
    if proc.returncode != 0:
        print("curl 失败 (exit=%d): %s" % (proc.returncode, proc.stdout.decode("utf-8", "replace")))
        return False
    if not os.path.exists(LOCAL_OUT):
        print("curl 报告成功但本地没有文件")
        return False
    print("已保存到 %s（%d 字节）" % (LOCAL_OUT, os.path.getsize(LOCAL_OUT)))
    return True


def show(max_lines_per_group=40, max_line_len=160):
    """打印结果：按组截断，避免刷屏。"""
    if not os.path.exists(LOCAL_OUT):
        print("本地没有 info.txt，无法打印")
        return
    with open(LOCAL_OUT, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")

    group = "(头部)"
    count = 0
    for line in lines:
        if line.startswith("##########"):
            group = line.strip("# ").strip()
            print("\n========== %s ==========" % group)
            count = 0
            continue
        if count >= max_lines_per_group:
            continue
        if len(line) > max_line_len:
            line = line[:max_line_len] + " ...(截断)"
        print(line)
        count += 1
    print("\n(完整内容见 %s)" % LOCAL_OUT)


def main(argv):
    ip = argv[1] if len(argv) > 1 else "192.168.5.39"
    print("=== 探测 %s 可用信息 ===" % ip)
    try:
        run_all(ip)
    except Exception as exc:
        print("telnet 阶段出错: %r" % (exc,))
    if fetch(ip):
        show()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
