#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_st_capture_test.py —— 通过 telnet(23, root 免密) 探索并调用 NX500 自带的 `st` 调试命令。

为什么要这个脚本
----------------
NX500 里有个隐藏的调试工具 `st`（`st cap`、`st bat`、`st pmu` 等模块），
但它的不少能力被相机 App 占用的 MCB 总线挡住（例如 `st cap lens info` 会回
`Cann't execute capture command over MCB!`）。这个脚本把探测过程脚本化：
连上去、按顺序跑命令、把原始输出打印到标准输出并汇总成一份报告。

真机踩过的坑（客户端实现里都处理了）
------------------------------------
1) **telnet IAC 协商**：相机 telnetd 一上来就发
   `ff fd 01  ff fd 1f  ff fb 01  ff fb 03`（DO ECHO / DO NAWS / WILL ECHO / WILL SGA），
   然后开启回显。客户端必须把这些协商字节整段吞掉，否则会污染正文和提示符匹配。
   规则：255(IAC) 后跟 0xFB..0xFE 吞 3 字节，其它命令吞 2 字节，IAC IAC 表示真正的 0xFF。
2) **登录提示**是 `drime5 login:`，必须先等它出现再发 `root`，然后等提示符。
3) **提示符正则必须是 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（匹配行尾）**，不能用 `"# " in buf`：
   实测后续提示符是 `[root@drime5 ~]#`（`#` 后面**没有空格**，被内核吞了），
   只有首个提示符带空格。用子串匹配会一直等不到。
4) 提示符里可能带 ANSI 颜色码，剥提示符前要先去 `\\x1b\\[[0-9;]*[A-Za-z]`。
5) 读取要**按行缓冲**：socket 超时只代表“暂时没数据”，读到一半的行必须留在缓冲区里，
   不能因为一次 timeout 就把半行丢掉（否则长命令输出会缺行、提示符会认不出来）。
6) 报告必须写到 SD 卡（默认 `/mnt/mmc/`）：**FTP 不提供 `/tmp` 和 `/var/log`**。
   写完后用 `curl.exe --user root: ftp://<ip>/<path>` 取回本地。
7) 相机上**没有 `timeout` 命令**（busybox 未编入），本脚本不使用它，超时全靠客户端自己控制。

用法
----
    python camera_st_capture_test.py --host 192.168.5.39
    python camera_st_capture_test.py --host 192.168.5.39 --cmd "st cap capt" --cmd "st bat get_soc"
    python camera_st_capture_test.py --self-test          # 不连相机，只自检 IAC/提示符逻辑
    python camera_st_capture_test.py --dry-run            # 只打印将要执行的命令
默认命令清单见 DEFAULT_COMMANDS；会真正触发快门/改设置的命令（如 `st cap capt single start`）
默认不跑，需要显式加 `--allow-shutter`。
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# telnet 协议常量
# --------------------------------------------------------------------------
IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240
NEGOTIATION = (WILL, WONT, DO, DONT)      # 0xFB..0xFE

# 真机 telnetd 的首包（写在这里当自检样本）
CAMERA_TELNET_GREETING = b"\xff\xfd\x01\xff\xfd\x1f\xff\xfb\x01\xff\xfb\x03"

# 提示符：可选的 [user@host dir] 前缀 + # + 可选空白，且必须在行尾。
# 注意：不能用 "# " 子串判断 —— 后续提示符 "[root@drime5 ~]#" 没有尾随空格。
PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.MULTILINE)
PROMPT_LINE_RE = re.compile(r"^\s*(\[[^\]]*\]\s*)?#\s*$")
LOGIN_RE = re.compile(r"login:\s*$", re.MULTILINE)
PASSWORD_RE = re.compile(r"[Pp]assword:\s*$", re.MULTILINE)
# ANSI/CSI 颜色码 + OSC 标题序列 + 字符集切换
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


class TelnetError(Exception):
    pass


# --------------------------------------------------------------------------
# 纯函数：IAC 过滤 / 提示符识别 / 文本清洗（都能离线自检）
# --------------------------------------------------------------------------
def filter_iac(data, pending=b""):
    """
    过滤 telnet IAC 协商字节。

    返回 (过滤后的正文 bytes, 需要留到下一批的 pending bytes)。
    * IAC + WILL/WONT/DO/DONT(0xFB..0xFE) + 1 字节选项 -> 吞 3 字节
    * IAC + 其它单字节命令                        -> 吞 2 字节
    * IAC IAC                                     -> 保留一个真正的 0xFF
    * IAC SB ... IAC SE                           -> 整段吞掉
    * 序列被 recv 切断时，把半截留在 pending 里等下一批（不能丢！）
    """
    buf = pending + data
    out = bytearray()
    i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:                      # 只有 IAC，后面的命令字节还没到
            return bytes(out), buf[i:]
        cmd = buf[i + 1]
        if cmd in NEGOTIATION:
            if i + 2 >= n:                  # 选项字节还没到
                return bytes(out), buf[i:]
            i += 3
        elif cmd == IAC:
            out.append(IAC)
            i += 2
        elif cmd == SB:                     # 子协商：一直找到 IAC SE
            j = i + 2
            while True:
                if j + 1 >= n:
                    return bytes(out), buf[i:]
                if buf[j] == IAC and buf[j + 1] == SE:
                    j += 2
                    break
                if buf[j] == IAC and buf[j + 1] == IAC:
                    j += 2
                    continue
                j += 1
            i = j
        else:
            i += 2
    return bytes(out), b""


def tail_match(rx, text):
    """在 text 中找 rx 的匹配，但要求匹配之后只剩空白（即匹配处在缓冲区末尾）。"""
    for m in rx.finditer(text):
        if not text[m.end():].strip():
            return m
    return None


def find_prompt(text):
    """提示符必须在缓冲区最后，避免把输出正文里的 '#' 误判成提示符。"""
    return tail_match(PROMPT_RE, text)


def strip_ansi(text):
    return ANSI_RE.sub("", text)


def clean_block(raw, command=""):
    """
    把一段原始回显清洗成“命令输出”：
    去 ANSI、统一换行、去掉首行命令回显（相机 telnetd 开回显）、去掉结尾提示符行。
    提示符若被内核吞掉尾随空格，回显前会多一个空行，这里一并处理。
    """
    text = strip_ansi(raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    cmd = command.strip()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and cmd and lines[0].strip() == cmd:
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and PROMPT_LINE_RE.match(lines[-1]):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# --------------------------------------------------------------------------
# telnet 客户端
# --------------------------------------------------------------------------
class TelnetClient:
    """够用的 telnet 客户端：IAC 过滤 + 行缓冲 + 提示符同步。"""

    def __init__(self, host, port=23, timeout=15.0, newline="\n", debug=False,
                 log=print, refuse_negotiation=False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.newline = newline
        self.debug = debug
        self.log = log
        self.refuse_negotiation = refuse_negotiation
        self.sock = None
        self._pending_iac = b""                       # 跨 recv 的半截 IAC 序列
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._text = ""                               # 行缓冲：读到一半的行留在这里
        self.events = []

    # ---- 连接 ----
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._note("connect %s:%d" % (self.host, self.port))
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
            self._note("closed")

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _note(self, msg):
        self.events.append((time.time(), msg))
        if self.debug:
            self.log("[telnet] %s" % msg)

    # ---- 收发 ----
    def _feed(self, chunk):
        body, self._pending_iac = filter_iac(chunk, self._pending_iac)
        if body:
            # 增量解码：多字节字符被 recv 切断也不会乱码
            self._text += self._decoder.decode(body)

    def _fill(self, timeout):
        """recv 一次。返回 False 表示对端关闭；socket 超时不是错误。"""
        self.sock.settimeout(max(0.05, min(timeout if timeout > 0 else 0.05, 1.0)))
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return True                                # 超时 -> 缓冲区原样保留（半行不丢）
        except OSError as exc:
            raise TelnetError("socket 读取失败: %s" % exc)
        if not chunk:
            return False
        self._feed(chunk)
        return True

    def _read_until(self, matcher, timeout=None):
        """循环收数据直到 matcher(self._text) 命中；超时返回 None（缓冲区不清空）。"""
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while True:
            m = matcher(self._text)
            if m is not None:
                out = self._text[:m.end()]
                self._text = self._text[m.end():]
                return out
            left = deadline - time.time()
            if left <= 0:
                return None
            if not self._fill(left):
                raise TelnetError("连接被相机关闭（缓冲区还剩 %d 字符）" % len(self._text))

    def read_until(self, pattern, timeout=None):
        """按正则读；pattern 可以是字符串或编译好的正则。"""
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return self._read_until(lambda t: rx.search(t), timeout)

    def read_until_tail(self, rx, timeout=None):
        rx = re.compile(rx) if isinstance(rx, str) else rx
        return self._read_until(lambda t: tail_match(rx, t), timeout)

    def read_until_prompt(self, timeout=None):
        """读到提示符（匹配行尾的正则），返回含提示符的整段文本；超时返回 None。"""
        return self._read_until(find_prompt, timeout)

    def pending_text(self):
        return self._text

    def drain(self, quiet=0.2):
        """把已经到达的残渣读掉丢掉（不清空半行，只做一次短等）。"""
        deadline = time.time() + quiet
        while time.time() < deadline:
            if not self._fill(deadline - time.time()):
                raise TelnetError("连接被相机关闭")
        self._text = ""

    def send_line(self, line):
        if self.sock is None:
            raise TelnetError("还没 connect()")
        payload = (line + self.newline).encode("utf-8", "replace")
        self.sock.sendall(payload)
        self._note("send %r" % (line if len(line) < 80 else line[:77] + "..."))

    # ---- 登录 / 执行 ----
    def login(self, user="root", password="", timeout=15.0):
        """等 'drime5 login:' -> 发用户名 -> （若被问）发密码 -> 等提示符。"""
        banner = self.read_until_tail(LOGIN_RE, timeout=timeout)
        if banner is None:
            raise TelnetError("没等到登录提示（期望 'drime5 login:'），缓冲区内容：%r" % self._text[-200:])
        self.send_line(user)
        if password:
            if self.read_until_tail(PASSWORD_RE, timeout=min(timeout, 5.0)):
                self.send_line(password)
        prompt = self.read_until_prompt(timeout=timeout)
        if prompt is None:
            raise TelnetError("输入用户名后没等到提示符，缓冲区内容：%r" % self._text[-200:])
        self._note("login ok, prompt=%r" % clean_block(prompt).split("\n")[-1:])
        return prompt

    def run(self, command, timeout=25.0, settle=0.12):
        """跑一条命令，返回清洗后的输出；超时返回 None（原始内容留在缓冲区）。"""
        self.drain(settle)
        self.send_line(command)
        raw = self.read_until_prompt(timeout=timeout)
        if raw is None:
            return None
        return clean_block(raw, command)

    def send_block(self, lines, pacing=0.02, timeout=60.0, settle=0.15):
        """
        逐行发送一大段文本（用于 heredoc 写文件），最后等一次提示符。
        慢一点发是故意的：tty 的规范模式缓冲区有限，猛灌会丢数据。
        """
        self.drain(settle)
        for line in lines:
            self.send_line(line)
            if pacing > 0:
                time.sleep(pacing)
        raw = self.read_until_prompt(timeout=timeout)
        return raw


# --------------------------------------------------------------------------
# 命令清单
# --------------------------------------------------------------------------
# (命令, 说明)  —— 都是本项目在真机上实际查过的
DEFAULT_COMMANDS = [
    ("st", "裸 st：列出可用模块"),
    ("st cap", "cap 模块用法"),
    ("st cap capt", "capt 子命令列表（如 single start / quality / size / ois / caf）"),
    ("st cap capt quality", "查询当前画质（不带参数 = 只读）"),
    ("st cap capt size", "查询图像尺寸（只读）"),
    ("st cap capt ois", "查询光学防抖状态（只读）"),
    ("st cap capt caf", "查询连续自动对焦状态（只读）"),
    ("st bat sel 1", "选择电池 1；真机实测返回 ret=-1（硬件不给电量百分比）"),
    ("st bat get_soc", "读电量百分比；实测 ret=-1"),
    ("st bat get_voltage", "读电压；实测 ret=-1"),
    ("st cap lens info", "实测返回 Cann't execute capture command over MCB!（相机 App 占着 MCB 总线）"),
    ("st pmu show", "模块供电状态表"),
    ("cat /etc/version.info", "固件版本信息"),
    ("date", "相机系统时间"),
    ("df -h /mnt/mmc", "SD 卡剩余空间"),
]
# 会真的按快门 / 改设置的命令，默认不跑
SHUTTER_COMMANDS = [
    ("st cap capt single start", "单张拍摄（会真的拍照！）"),
]


# --------------------------------------------------------------------------
# 报告 + 写回相机 + FTP 取回
# --------------------------------------------------------------------------
def sanitize_for_heredoc(text, delimiter, max_line=180):
    """
    把文本整理成适合 heredoc 的形式：
    * 去掉控制字符（保留 \\t）、统一换行；
    * 万一正文里出现结束标记，替换掉，避免提前结束 heredoc；
    * 超长行硬折行（tty 规范模式的行缓冲有限，一行几千字节会被截断）。
    """
    safe = delimiter.replace("_", "-")
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = "".join(ch for ch in raw if ch == "\t" or ord(ch) >= 32)
        line = line.replace(delimiter, safe)
        while len(line) > max_line:
            out.append(line[:max_line] + " \\[wrapped]")
            line = line[max_line:]
        out.append(line)
    return out


def build_report(host, port, results, t0, truncated_note=""):
    lines = [
        "==== NX500 `st` 探针报告 ====",
        "相机        : %s:%d (telnet, root 免密)" % (host, port),
        "本地时间    : %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
        "耗时        : %.1fs" % (time.time() - t0),
        "说明        : 每段以 '--- $ <命令>' 开头，下面是该命令的原始输出。",
        "              相机上没有 timeout 命令（busybox 未编入），本脚本不用它。",
        "              报告写在本文件里（SD 卡 /mnt/mmc），再经 FTP 取回本地。",
    ]
    if truncated_note:
        lines.append("注意        : %s" % truncated_note)
    lines.append("")
    for cmd, note, out in results:
        lines.append("--- $ %s%s" % (cmd, ("    # " + note) if note else ""))
        if out is None:
            lines.append("(超时：没等到提示符，输出可能不完整)")
        else:
            body = out.rstrip("\n")
            lines.extend(body.split("\n") if body.strip() else ["(无输出)"])
        lines.append("")
    return "\n".join(lines) + "\n"


def write_remote_text(tc, remote_path, text, delimiter="__NXC_REPORT_EOF__", pacing=0.02):
    """用 heredoc 把文本写到相机（单引号 heredoc -> 不做变量展开、不解析 $ 和反引号）。"""
    lines = sanitize_for_heredoc(text, delimiter)
    if delimiter in lines:
        raise TelnetError("正文里出现了 heredoc 结束标记，换个 delimiter")
    block = ["cat > %s <<'%s'" % (remote_path, delimiter)] + lines + [delimiter]
    tc.send_block(block, pacing=pacing)
    return len(lines)


def run_remote(tc, command, timeout=25.0):
    out = tc.run(command, timeout=timeout)
    return "" if out is None else out


def verify_remote_file(tc, remote_path, local_text):
    """用 wc -c / md5sum 校验写到相机上的报告和本地是否一致。"""
    local_bytes = local_text.encode("utf-8")
    try:
        remote_md5 = run_remote(tc, "md5sum %s" % remote_path).split()
        remote_size = run_remote(tc, "wc -c < %s" % remote_path).strip().split()
    except TelnetError as exc:
        return False, "校验命令失败: %s" % exc
    ok = True
    detail = []
    if remote_size:
        try:
            got = int(remote_size[0])
            detail.append("远端 %d 字节 / 本地 %d 字节" % (got, len(local_bytes)))
            ok = ok and got == len(local_bytes)
        except ValueError:
            detail.append("wc 输出无法解析: %r" % remote_size)
    if remote_md5 and len(remote_md5[0]) == 32:
        want = hashlib.md5(local_bytes).hexdigest()
        detail.append("md5 %s / %s" % (remote_md5[0], want))
        ok = ok and remote_md5[0].lower() == want
    else:
        detail.append("相机上没有 md5sum，只用 wc -c 校验")
    return ok, "；".join(detail)


def ftp_fetch(host, remote_path, local_path, user="root", curl="curl.exe",
              ftp_root_map="/mnt/mmc=/", timeout=60):
    """
    用 curl.exe 从 FTP 取回文件。FTP 根目录就是 SD 卡（真机 /opt/storage/sdcard），
    所以 /mnt/mmc/nx_st_probe.txt 在 FTP 里是 /nx_st_probe.txt。
    取不到再退回按字面路径试一次。
    """
    exe = shutil.which(curl) or shutil.which("curl")
    if not exe:
        return False, "找不到 curl（%s），请手动取回 ftp://%s%s" % (curl, host, remote_path)

    mapped = remote_path
    src = ftp_root_map.split("=", 1)[0] if "=" in ftp_root_map else ""
    if src and mapped.startswith(src):
        mapped = mapped[len(src):] or "/"
    candidates = ["ftp://%s/%s" % (host, mapped.lstrip("/"))]
    if mapped != remote_path:
        candidates.append("ftp://%s/%s" % (host, remote_path.lstrip("/")))

    last = ""
    for url in candidates:
        cmd = [exe, "--silent", "--show-error", "--user", "%s:" % user,
               "--max-time", str(timeout), "-o", local_path, url]
        print("  $ %s" % " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
        except Exception as exc:                        # noqa: BLE001
            last = "curl 执行失败: %s" % exc
            continue
        if proc.returncode == 0 and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return True, "%s -> %s (%d 字节)" % (url, local_path, os.path.getsize(local_path))
        last = "curl rc=%d stdout=%r stderr=%r" % (proc.returncode, proc.stdout.strip(), proc.stderr.strip())
        if os.path.exists(local_path) and os.path.getsize(local_path) == 0:
            os.remove(local_path)
    return False, last


# --------------------------------------------------------------------------
# 自检（不需要相机）
# --------------------------------------------------------------------------
def self_test(log=print):
    failed = 0

    def check(name, ok, detail=""):
        nonlocal failed
        if not ok:
            failed += 1
        log("%s  %-42s %s" % ("PASS" if ok else "FAIL", name, detail))

    # 1) 真机首包必须被整段吞掉
    out, pending = filter_iac(CAMERA_TELNET_GREETING + b"drime5 login: ")
    check("IAC 过滤：真机首包", out == b"drime5 login: " and pending == b"",
          "%r pending=%r" % (out, pending))

    # 2) 协商序列被 recv 切断（半截必须留给下一批）
    out1, pen1 = filter_iac(b"\xff\xfd\x01\xff")
    out2, pen2 = filter_iac(b"\xfd\x1f hi", pen1)
    check("IAC 过滤：跨 recv 切断", out1 == b"" and pen1 == b"\xff" and out2 == b" hi" and pen2 == b"",
          "%r/%r -> %r/%r" % (out1, pen1, out2, pen2))

    # 3) IAC IAC = 真正的 0xFF；IAC SB ... IAC SE 整段吞
    out, _ = filter_iac(b"a\xff\xffb")
    check("IAC 过滤：IAC IAC -> 0xFF", out == b"a\xffb", repr(out))
    out, _ = filter_iac(b"\xff\xfa\x1f\x00\x50\x00\x18\xff\xf0ok")
    check("IAC 过滤：子协商 SB..SE", out == b"ok", repr(out))

    # 4) 提示符正则：后续提示符没有尾随空格，必须能匹配
    check("提示符：[root@drime5 ~]# 行尾", bool(find_prompt("[root@drime5 ~]#")),
          repr(find_prompt("[root@drime5 ~]#")))
    check("提示符：首个提示符带空格", bool(find_prompt("[root@drime5 ~]# ")),
          repr(find_prompt("[root@drime5 ~]# ")))
    check("提示符：提示符后有残留内容则不算",
          find_prompt("[root@drime5 ~]#\nls -l") is None, "")
    # 不能用 "# " in buf：正文里的 '#' 不能当提示符
    check("提示符：正文中的 '#' 不误判", find_prompt("total 0 # 注释") is None, "")
    check("提示符：'# ' 子串判断对真机提示符会失败（回归）",
          not ("# " in "[root@drime5 ~]#"), '"# " in "[root@drime5 ~]#" == False')

    # 5) 剥 ANSI + 清洗回显（真实形状：命令回显 -> 输出 -> 带颜色码的提示符）
    raw = "date\r\nThu Jan  1 00:00:00 UTC 2026\r\n\x1b[0;32m[root@drime5 ~]#\x1b[0m "
    cleaned = clean_block(raw, "date")
    check("清洗：去 ANSI/回显/提示符", cleaned == "Thu Jan  1 00:00:00 UTC 2026", repr(cleaned))
    # 提示符没有尾随空格时，回显前面会多一个空行
    cleaned2 = clean_block("\r\ndf -h /mnt/mmc\r\n/dev/mmcblk0 29G 12G 17G\r\n[root@drime5 ~]#", "df -h /mnt/mmc")
    check("清洗：提示符无尾随空格时多出的空行", cleaned2 == "/dev/mmcblk0 29G 12G 17G", repr(cleaned2))

    # 6) 半行不能因为超时被丢掉
    tc = TelnetClient("127.0.0.1", timeout=0.3, log=log)
    tc._feed("partial".encode())            # 没有换行的一行
    check("行缓冲：半行保留", tc.pending_text() == "partial", repr(tc.pending_text()))

    # 7) heredoc 清洗：结束标记必须不会提前出现
    lines = sanitize_for_heredoc("a\n__X__\nb", "__X__")
    check("heredoc：结束标记被替换", "__X__" not in lines, repr(lines))
    check("heredoc：超长行折行", all(len(x) <= 200 for x in sanitize_for_heredoc("L" * 500, "Z")),
          "最长 %d" % max(len(x) for x in sanitize_for_heredoc("L" * 500, "Z")))
    return failed


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="NX500 的 st 调试命令探测（telnet 23, root 免密）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例：python camera_st_capture_test.py --host 192.168.5.39 --cmd \"st pmu show\"",
    )
    p.add_argument("--host", default="192.168.5.39", help="相机 IP（默认 192.168.5.39）")
    p.add_argument("--port", type=int, default=23, help="telnet 端口，真机 23")
    p.add_argument("--user", default="root", help="登录用户，真机 root 免密")
    p.add_argument("--password", default="", help="root 免密，留空即可")
    p.add_argument("--timeout", type=float, default=20.0, help="单条命令等待提示符的秒数")
    p.add_argument("--cmd", action="append", default=[], help="追加命令（可多次），不写则用默认清单")
    p.add_argument("--allow-shutter", action="store_true",
                   help="额外执行会真的按快门的命令（st cap capt single start）")
    p.add_argument("--skip-capt-subs", action="store_true", help="跳过 st cap capt quality/size/ois/caf")
    p.add_argument("--max-capture", type=int, default=8000, help="单条命令最多记录多少字节输出")
    p.add_argument("--out-dir", default="/mnt/mmc", help="报告写到相机上的目录（必须 SD 卡：FTP 没有 /tmp、/var/log）")
    p.add_argument("--remote-name", default="", help="远端文件名，默认 nx_st_probe_<时间戳>.txt")
    p.add_argument("--local-out", default="", help="取回本地的文件名，默认与远端同名")
    p.add_argument("--no-write", action="store_true", help="不往相机写报告（只打印）")
    p.add_argument("--no-ftp", action="store_true", help="不用 curl 取回（报告留在相机上）")
    p.add_argument("--ftp-map", default="/mnt/mmc=/", help="远端目录到 FTP 根的映射（FTP 根 = SD 卡）")
    p.add_argument("--curl", default="curl.exe", help="curl 可执行文件（Windows 上是 curl.exe）")
    p.add_argument("--pacing", type=float, default=0.02, help="写 heredoc 时每行之间的间隔秒数")
    p.add_argument("--crlf", action="store_true", help="发送 CRLF（默认只发 LF）")
    p.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令，不连相机")
    p.add_argument("--self-test", action="store_true", help="离线自检 IAC/提示符/清洗逻辑")
    p.add_argument("--debug", action="store_true", help="打印 telnet 收发细节")
    return p.parse_args(argv)


def build_commands(args):
    if args.cmd:
        cmds = [(c, "命令行指定") for c in args.cmd]
    else:
        cmds = list(DEFAULT_COMMANDS)
        if args.skip_capt_subs:
            cmds = [c for c in cmds if not c[0].startswith("st cap capt ")]
    if args.allow_shutter:
        cmds += [(c, n) for c, n in SHUTTER_COMMANDS]
    return cmds


def main(argv=None):
    args = parse_args(argv)

    if args.self_test:
        print("=== camera_st_capture_test.py 自检（不连相机）===")
        failed = self_test()
        print("--- %s" % ("全部通过" if not failed else "%d 项失败" % failed))
        return 1 if failed else 0

    commands = build_commands(args)
    if args.dry_run:
        print("将要执行（host=%s port=%d user=%s）：" % (args.host, args.port, args.user))
        for cmd, note in commands:
            print("  %-28s # %s" % (cmd, note))
        print("报告会写到：%s/%s" % (args.out_dir, args.remote_name or "nx_st_probe_<时间戳>.txt"))
        return 0

    t0 = time.time()
    remote_name = args.remote_name or time.strftime("nx_st_probe_%Y%m%d_%H%M%S.txt")
    remote_path = "%s/%s" % (args.out_dir.rstrip("/"), remote_name)
    local_out = args.local_out or remote_name

    results = []
    tc = TelnetClient(args.host, args.port, timeout=args.timeout, debug=args.debug,
                      newline="\r\n" if args.crlf else "\n")
    print("=== NX500 st 探针 -> %s:%d ===" % (args.host, args.port))
    try:
        tc.connect()
        tc.login(args.user, args.password, timeout=max(args.timeout, 15.0))
        print("登录成功（root 免密）\n")

        for cmd, note in commands:
            print("-" * 72)
            print("$ %s    # %s" % (cmd, note))
            try:
                out = tc.run(cmd, timeout=args.timeout)
            except TelnetError as exc:
                print("[telnet 错误] %s" % exc)
                out = None
            if out is None:
                print("(超时：没等到提示符 %r)" % PROMPT_RE.pattern)
            else:
                if len(out.encode("utf-8", "replace")) > args.max_capture:
                    out = out.encode("utf-8", "replace")[:args.max_capture].decode("utf-8", "replace")
                    out += "\n...[已截断，--max-capture=%d]" % args.max_capture
                print(out if out.strip() else "(无输出)")
            results.append((cmd, note, out))
    except (OSError, TelnetError) as exc:
        print("连接/登录失败：%s" % exc)
        print("提示：确认相机已开机、遥控模式已开（nx-remote-controller-daemon 在跑），"
              "并且 telnet 23 端口通：telnet %s 23" % args.host)
        tc.close()
        return 2

    report = build_report(args.host, args.port, results, t0)
    print("=" * 72)
    print("原始输出如上；报告共 %d 命令 / %d 字节" % (len(results), len(report.encode("utf-8"))))

    ok = True
    if not args.no_write:
        try:
            n_lines = write_remote_text(tc, remote_path, report, pacing=args.pacing)
            print("已写入相机：%s（%d 行 heredoc；FTP 没有 /tmp 和 /var/log，所以必须写 SD 卡）"
                  % (remote_path, n_lines))
            good, detail = verify_remote_file(tc, remote_path, report)
            print("远端校验：%s -> %s" % ("OK" if good else "不一致", detail))
            ok = ok and good
        except TelnetError as exc:
            print("写报告失败：%s" % exc)
            ok = False

    if not args.no_write and not args.no_ftp:
        print("用 FTP 取回（--user root:）：")
        good, detail = ftp_fetch(args.host, remote_path, local_out, user=args.user,
                                 curl=args.curl, ftp_root_map=args.ftp_map)
        print("FTP %s：%s" % ("OK" if good else "失败", detail))
        if good:
            try:
                with open(local_out, "rb") as fp:
                    local_md5 = hashlib.md5(fp.read()).hexdigest()
                print("本地文件 %s md5=%s" % (local_out, local_md5))
            except OSError as exc:
                print("读回本地文件失败：%s" % exc)
        ok = ok and good

    tc.close()
    print("总结：%d 条命令，%d 条超时/失败" % (len(results), sum(1 for _, _, o in results if o is None)))
    print("相机上没有 `timeout` 命令（busybox 未编入），所以超时一律由本脚本自己控制。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
