#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_telnet_raw.py —— 相机 23 端口 telnet 的"原始字节级"诊断工具。

这个脚本当初就是为了看清 IAC 才写的。
现象是：用普通字符串匹配死活等不到提示符，读到的东西 print 出来是乱码，
但用 repr() 一看才发现前面挂着 \xff\xfd\x01\xff\xfd\x1f\xff\xfb\x01\xff\xfb\x03
（telnet 协商），而且命令本身还被回显了一遍。
所以这里每一步都 recv 一次并 dump repr()，把 IAC 字节与回显看得一清二楚。

流程（每步独立 drain + dump）：
    1) 刚连上           -> banner + IAC 协商
    2) 发 root 之后      -> login 回显
    3) 发一个换行之后    -> 首个提示符（注意它带尾随空格）
    4) echo HELLO       -> 看回显与输出
    5) 发 begin 标记     -> 验证标记行能不能被行尾正则匹配
    6) 发命令            -> 真实命令的原始输出
    7) 发 end 标记       -> 验证结束标记

用法：
    python camera_telnet_raw.py 192.168.5.39
    python camera_telnet_raw.py 192.168.5.39 "date '+%Z %z'"
"""

import socket
import sys
import time

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250

TELNET_NAMES = {251: "WILL", 252: "WONT", 253: "DO", 254: "DONT", 250: "SB", 255: "IAC"}


class RawCam(object):
    """不带任何智能解析的裸 socket —— 故意不做 IAC 过滤，方便看原始字节。"""

    def __init__(self, ip, port=23):
        self.ip = ip
        self.port = port
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), 5.0)
        # 0.6 秒：够短，能快速结束一步；又够长，局域网内不会漏掉相机的一次输出
        self.sock.settimeout(0.6)
        return self

    def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        self.sock.sendall(data)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    # ---------------------------------------------------------------- 核心 dump
    def drain(self, seconds, label):
        """在 seconds 秒内反复 recv，收集所有字节，然后 dump repr()。

        按"块"收集而不是拼成一个大字符串，这样能看清相机是分几次发来的、
        每次发的是什么（比如 IAC 协商是单独一块，提示符是另一块）。
        """
        deadline = time.time() + seconds
        blocks = []
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                print("  [%s] recv 出错: %r" % (label, exc))
                break
            if not chunk:
                print("  [%s] 对端已关闭连接" % label)
                break
            blocks.append(chunk)

        print("---- %s ----" % label)
        if not blocks:
            print("  (无数据)")
        for idx, chunk in enumerate(blocks):
            print("  #%d len=%d" % (idx, len(chunk)))
            print("    repr : %r" % (chunk,))          # 关键：原始字节一目了然
            print("    text : %s" % chunk.decode("utf-8", "replace").replace("\n", "\\n"))
            iac = [b for b in chunk if b == IAC]
            if iac:
                print("    >> 本块含 %d 个 IAC(255) 字节" % len(iac))
        print("  (块数=%d, 总字节=%d)" % (len(blocks), sum(len(b) for b in blocks)))
        print()
        return b"".join(blocks)


def describe_iac(raw):
    """把一段原始字节里所有 IAC 序列解释成人话，帮助确认过滤规则。"""
    if IAC not in raw:
        return
    print("---- IAC 序列解读 ----")
    i = 0
    out = []
    while i < len(raw):
        if raw[i] == IAC and i + 1 < len(raw):
            cmd = raw[i + 1]
            name = TELNET_NAMES.get(cmd, "CMD(%d)" % cmd)
            if 0xFB <= cmd <= 0xFE and i + 2 < len(raw):
                out.append("%s 选项=%d  => 吞 3 字节" % (name, raw[i + 2]))
                i += 3
            else:
                out.append("%s  => 吞 2 字节" % name)
                i += 2
            continue
        i += 1
    for line in out:
        print("  " + line)
    print()


def main(argv):
    ip = argv[1] if len(argv) > 1 else "192.168.5.39"
    cmd = argv[2] if len(argv) > 2 else "echo HELLO_FROM_RAW"

    print("=== 连接 %s:23（原始字节诊断）===" % ip)
    cam = RawCam(ip)
    try:
        cam.connect()
    except Exception as exc:
        print("连接失败: %r" % (exc,))
        return 1

    try:
        step1 = cam.drain(1.5, "步骤1: 刚连上（banner + IAC 协商）")
        describe_iac(step1)

        cam.send("root\n")
        cam.drain(1.0, "步骤2: 发出 root 之后（回显 + 可能的 Password 提示）")

        cam.send("\n")
        cam.drain(1.0, "步骤3: 再发一个换行之后（首个提示符，通常带尾随空格）")

        cam.send("echo HELLO\n")
        cam.drain(1.2, "步骤4: echo HELLO（注意命令回显）")

        begin = "===BEGIN_RAW_%d===" % int(time.time())
        end = "===END_RAW_%d===" % int(time.time())
        cam.send("echo %s\n" % begin)
        cam.drain(1.0, "步骤5: 发出 begin 标记")

        cam.send(cmd + "\n")
        cam.drain(1.5, "步骤6: 执行命令 %r" % cmd)

        cam.send("echo %s\n" % end)
        cam.drain(1.0, "步骤7: 发出 end 标记")

        print("提示：把上面的 repr 与这两点对照——")
        print("  1) \\xff\\xfd\\x01 这类 IAC 必须按 3 字节吞掉（WILL/WONT/DO/DONT + 选项）。")
        print("  2) 后续提示符 '[root@drime5 ~]#' 可能没有尾随空格，")
        print("     所以匹配要用行尾正则 r'(\\[[^\\]]*\\]\\s*)?#\\s*$'，不能用 '# ' in buf。")
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
