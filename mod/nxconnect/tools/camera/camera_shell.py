#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_shell.py —— 三星 NX500 相机的最小可复用 telnet shell 客户端 + 命令行工具。

相机端口：23 = telnet（用户名 root，密码为空）。

真机踩过的两个大坑（改动这里之前请先读完）：

  坑 1：IAC 协商字节必须过滤
     相机 telnetd 一连上就主动推协商序列：
         \xff\xfd\x01 \xff\xfd\x1f \xff\xfb\x01 \xff\xfb\x03
     而且它默认开启回显（我们发过去的每个字符都会被回显一遍）。
     过滤规则：遇到 255 (IAC) 时，读下一个字节当命令；
       若命令落在 0xFB..0xFE (WILL/WONT/DO/DONT)，说明后面还有一个选项字节，
       整段吞掉 3 字节；否则只吞 2 字节。
     不做这层过滤，命令回显里就会混进 \xff 之类的字节，
     后续所有字符串匹配都会莫名其妙失败。

  坑 2：提示符必须用"行尾正则"匹配，绝对不能用 "# " in buf
     实测第一个提示符是 "[root@drime5 ~]# "（带尾随空格），
     但后续提示符是 "[root@drime5 ~]#"，尾随空格被内核吞掉了。
     所以只要用 `"# " in buf` 判断，第一条命令能过，第二条就永远等不到，
     最后读到一堆乱码然后超时。
     正确做法：PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)，
     即在"行尾"处匹配可选的 [xxx] 前缀 + '#' + 可选空白。
     另外剥提示符前要先去掉 ANSI 颜色码，否则残留 \x1b[0;32m 之类。
     读取必须按行缓冲，不能因为 socket 超时把读到一半的行丢掉。

命令行用法：
    python camera_shell.py 192.168.5.39 "date '+%Z %z'"
    python camera_shell.py 192.168.5.39            # 进入简单交互循环
"""

import re
import socket
import sys
import time

IAC = 255          # Interpret As Command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250           # 子协商开始（相机一般不发，但顺手处理）

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")          # ANSI 颜色/光标码
PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)  # 坑 2：行尾提示符


class CamShell(object):
    """极简相机 telnet 客户端。

    典型用法：
        sh = CamShell("192.168.5.39")
        sh.login()
        print(sh.run("date '+%Z %z'"))
        sh.close()
    """

    def __init__(self, ip, port=23, timeout=6.0, debug=False):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.debug = debug
        self.sock = None
        self._buf = ""        # 行缓冲：可能包含半行，跨 recv 保留
        self._raw = bytearray()

    # ------------------------------------------------------------------ 连接
    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)
        self.sock.settimeout(0.6)   # 短超时用于 drain，不影响整体等待
        return self

    def login(self, user="root", password=""):
        """登录：相机 telnet 密码为空，直接发用户名即可。"""
        if self.sock is None:
            self.connect()
        # 等 login: 提示（不一定有，所以只是尽力而为）
        self._drain_until(("login:", "Password:", "#"), 3.0)
        self._send_raw(user + "\n")
        self._drain_until(("Password:", "#"), 2.0)
        if password:
            self._send_raw(password + "\n")
        # 等到 shell 提示符；收到第一个提示符后别忘了清空缓冲
        self._drain_until(("#",), 3.0)
        self._buf = ""
        self._raw = bytearray()
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    # ------------------------------------------------------------ 底层 IAC 过滤
    def _send_raw(self, text):
        if self.sock is None:
            raise RuntimeError("socket 未连接，请先 login()")
        self.sock.sendall(text.encode("utf-8", "replace"))

    def _recv_filtered(self):
        """收一次数据，剥掉 IAC 协商序列，返回可打印文本（可能不完整）。

        坑 1 的落点：处理 IAC 时用"读下一个字节当命令"的方式，
        WILL/WONT/DO/DONT 后面还有一个选项字节，所以要吞 3 字节。
        """
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return ""
        except OSError:
            return ""
        if not chunk:
            return ""

        out = bytearray()
        i = 0
        n = len(chunk)
        while i < n:
            b = chunk[i]
            if b == IAC:
                if i + 1 >= n:
                    break                      # 命令字节在下一次 recv 里，丢弃即可
                cmd = chunk[i + 1]
                if 0xFB <= cmd <= 0xFE:
                    i += 3                     # WILL/WONT/DO/DONT + 选项字节
                elif cmd == SB:
                    j = chunk.find(b"\xff\xf0", i)   # 找到 IAC SE
                    i = (j + 2) if j != -1 else n
                elif cmd == IAC:
                    out.append(IAC)            # 转义的 0xFF
                    i += 2
                else:
                    i += 2
                continue
            out.append(b)
            i += 1

        text = out.decode("utf-8", "replace")
        self._raw.extend(chunk)
        text = ANSI_RE.sub("", text)           # 顺手剥 ANSI 颜色码
        self._buf += text.replace("\r\n", "\n").replace("\r", "\n")
        return text

    def _drain_until(self, needles, seconds):
        """在 seconds 秒内持续收数据，直到缓冲命中任一 needle（或超时）。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._recv_filtered()
            for nd in needles:
                if nd in self._buf:
                    return True
        return False

    def read_until_prompt(self, seconds=6.0):
        """读到提示符为止，返回"剥掉提示符"的净输出。

        坑 2 的落点：用 PROMPT_RE.search(self._buf) 做行尾匹配，
        并且只在完整行上判断，避免把半行当成结果。
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._recv_filtered()
            m = PROMPT_RE.search(self._buf)
            if m:
                body = self._buf[:m.start()]
                self._buf = self._buf[m.end():]
                return self._strip_echo(body)
            time.sleep(0.02)
        # 超时：把已收到的内容交给上层，便于诊断
        body = self._buf
        self._buf = ""
        return self._strip_echo(body)

    @staticmethod
    def _strip_echo(body):
        """去掉回显残留与 ANSI，再剥掉可能残存的提示符。"""
        s = ANSI_RE.sub("", body)
        s = PROMPT_RE.sub("", s)
        return s.strip("\n")

    # ---------------------------------------------------------------- 高层接口
    def run(self, cmd, seconds=6.0):
        """执行一条命令，返回输出文本（不含提示符、不含命令回显行）。"""
        if self.sock is None:
            self.login()
        # 清掉上一条命令可能残留的东西
        self._buf = ""
        self._send_raw(cmd + "\n")
        out = self.read_until_prompt(seconds)
        # 丢掉命令自身的回显行
        lines = out.split("\n")
        if lines and lines[0].strip().endswith(cmd.strip()):
            lines = lines[1:]
        return "\n".join(lines).strip("\n")

    def run_raw(self, cmd, seconds=6.0):
        """执行命令但保留回显，用于排查协议问题。"""
        if self.sock is None:
            self.login()
        self._buf = ""
        self._send_raw(cmd + "\n")
        return self.read_until_prompt(seconds)


def interactive(shell):
    print("进入交互模式，输入命令回车执行；空行或 Ctrl-C / exit 退出。")
    while True:
        try:
            cmd = input("cam> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cmd or cmd in ("exit", "quit"):
            break
        print(shell.run(cmd))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        print("用法: python camera_shell.py <ip> [\"命令\"]")
        return 2
    ip = argv[1]
    shell = CamShell(ip)
    try:
        shell.login()
    except Exception as exc:
        print("连接/登录失败: %r" % (exc,))
        return 1
    try:
        if len(argv) >= 3:
            print(shell.run(" ".join(argv[2:])))
        else:
            interactive(shell)
    finally:
        shell.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
