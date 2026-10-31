#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_time_sync_final.py —— NX500 端到端时间同步脚本（最完整的那个）。

内含 TelnetReader 类：这是与 Kotlin 侧 `NxShell.TelnetReader` 逐字对应的 Python 复刻，
用来在真机上验证 Kotlin 实现是否正确。两条协议要点在这里都是"照抄"级别的一致：

    1) IAC 过滤：相机 telnetd 一连上就发
           \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03
       并且开启回显。遇 IAC(255) 读下一字节当命令；
       命令在 0xFB..0xFE (WILL/WONT/DO/DONT) 时吞 3 字节，否则吞 2 字节。

    2) 提示符行尾正则：r"(\\[[^\\]]*\\]\\s*)?#\\s*$"（re.M）
       实测后续提示符 "[root@drime5 ~]#" 没有尾随空格（被内核吞了），
       只有首个提示符带空格 —— 所以绝不能用 "# " in buf。

流程（保留历史步骤，便于对比）：
    A. 读同步前时间与偏差
    B. date -s @<epoch>       <-- 历史上这一步造成过 1 小时偏差，保留它是为了对比
    C. hwclock -w             <-- 预期失败，记录错误
    D. 复读校验
    E. 打印人类可读时间，给出结论

每一步都打印 begin/end 标记行的匹配情况（行号），便于确认协议本身没问题。

用法：
    python camera_time_sync_final.py 192.168.5.39
"""

import datetime
import re
import socket
import sys
import time

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)   # 与 Kotlin 侧一致


class TelnetReader(object):
    """与 Kotlin `NxShell.TelnetReader` 逐字对应的 Python 复刻。

    职责：
      - 从 socket 收字节，剥掉 IAC 协商序列（WILL/WONT/DO/DONT + 选项 = 3 字节）
      - 按行缓冲：socket 超时不会丢掉读到一半的行
      - 用行尾正则识别提示符，返回"剥掉提示符"的正文
    """

    def __init__(self, ip, port=23, socket_timeout=0.6):
        self.sock = socket.create_connection((ip, port), 5.0)
        self.sock.settimeout(socket_timeout)
        self.buf = ""
        # 诊断用：记录 begin/end 标记所在行号
        self.begin_line = None
        self.end_line = None

    # ------------------------------------------------------------------ IAC
    @staticmethod
    def _strip_iac(chunk):
        """剥 IAC 协商。返回 (纯文本字节, 是否在末尾遇到被截断的 IAC)。

        过滤规则（坑 1）：
            IAC + 0xFB..0xFE  -> 吞 3 字节（协商 + 选项）
            IAC + IAC          -> 转义，保留一个 0xFF
            其他 IAC + cmd     -> 吞 2 字节
        """
        out = bytearray()
        i, n = 0, len(chunk)
        while i < n:
            b = chunk[i]
            if b == IAC:
                if i + 1 >= n:
                    break                       # 不完整，直接丢弃这 1 字节
                cmd = chunk[i + 1]
                if 0xFB <= cmd <= 0xFE:
                    i += 3
                elif cmd == IAC:
                    out.append(IAC)
                    i += 2
                else:
                    i += 2
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def _pump(self):
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return False
        except OSError:
            return False
        if not chunk:
            return False
        text = self._strip_iac(chunk).decode("utf-8", "replace")
        text = ANSI_RE.sub("", text)
        # 统一换行，保证行尾正则在两种换行下都能工作
        self.buf += text.replace("\r\n", "\n").replace("\r", "\n")
        return True

    # -------------------------------------------------------------- 提示符
    def read_until_prompt(self, seconds=6.0):
        """读到提示符，返回正文；超时则返回已收到的内容（并清空缓冲）。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._pump()
            m = PROMPT_RE.search(self.buf)
            if m:
                body = self.buf[:m.start()]
                self.buf = self.buf[m.end():]
                return body
            time.sleep(0.02)
        body, self.buf = self.buf, ""
        return body

    def send(self, s):
        self.sock.sendall(s.encode("utf-8", "replace"))

    def run(self, cmd, seconds=6.0):
        """执行命令，返回剥掉回显与提示符的正文。"""
        self.buf = ""
        self.send(cmd + "\n")
        body = self.read_until_prompt(seconds)
        lines = body.split("\n")
        if lines and lines[0].strip().endswith(cmd.strip()):
            lines = lines[1:]
        return "\n".join(lines).strip("\n")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class CamSync(object):
    """基于 TelnetReader 的时间同步流程封装。"""

    def __init__(self, ip):
        self.ip = ip
        self.t = TelnetReader(ip)

    def login(self):
        self.t.read_until_prompt(1.5)     # banner + IAC 协商
        self.t.send("root\n")             # 用户名 root，密码为空
        self.t.read_until_prompt(1.0)
        self.t.send("\n")
        self.t.read_until_prompt(1.0)     # 首个提示符
        self.t.buf = ""
        return self

    def close(self):
        self.t.close()

    # --------------------------------------------------- 带 begin/end 标记的执行
    def marked_run(self, label, cmd, seconds=6.0):
        """用 begin/end 标记包住命令，打印标记行号以确认协议正确。"""
        begin = "===BEGIN_%d===" % int(time.time() * 1000)
        end = "===END_%d===" % int(time.time() * 1000)
        self.t.buf = ""
        self.t.send("echo %s\n" % begin)
        self.t.read_until_prompt(3.0)
        out = self.t.run(cmd, seconds)
        self.t.send("echo %s\n" % end)
        tail = self.t.read_until_prompt(3.0)

        lines = (out + "\n" + tail).split("\n")
        b_line = next((i for i, ln in enumerate(lines) if begin in ln), None)
        e_line = next((i for i, ln in enumerate(lines) if end in ln), None)
        print("    [%s] begin 标记行号=%s, end 标记行号=%s, 总行数=%d"
              % (label, b_line, e_line, len(lines)))
        # 只取 begin 与 end 之间的正文
        body = out
        if b_line is not None and e_line is not None and e_line >= b_line:
            body = "\n".join(lines[b_line:e_line])
        return body

    def cam_epoch(self):
        raw = self.t.run("date '+%s'")
        m = re.search(r"(\d{9,})", raw)
        return (int(m.group(1)) if m else None), raw


def human(epoch):
    try:
        return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "(无法转换: %r)" % (epoch,)


def main(argv):
    ip = argv[1] if len(argv) > 1 else "192.168.5.39"
    cam = CamSync(ip)
    try:
        cam.login()
        print("=== NX500 端到端时间同步 @ %s ===" % ip)

        # ---------- A. 同步前 ----------
        print("\n[A] 同步前时间与偏差")
        cam_str, _ = cam.cam_epoch()
        before_human = cam.marked_run("before", "date '+%Y-%m-%d %H:%M:%S'").strip()
        phone_epoch = int(time.time())
        phone_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("    相机: %s (epoch=%s / %s)" % (before_human, cam_str, human(cam_str) if cam_str else "?"))
        print("    手机: %s (epoch=%d)" % (phone_str, phone_epoch))
        if cam_str is not None:
            drift = phone_epoch - cam_str
            print("    偏差: %d 秒 (约 %.2f 年)" % (drift, drift / 31557600.0))
        tz = cam.marked_run("tz", "date '+%Z %z'").strip()
        print("    时区: %s" % tz)

        # ---------- B. date -s @epoch（历史步骤，保留用于对比）----------
        print("\n[B] date -s @%d（按 epoch 设置）" % phone_epoch)
        print("    历史背景：这一步会造成 1 小时偏差 ——")
        print("    相机时区是 KST(UTC+9)，而手机在 UTC+8；@epoch 是绝对时刻，")
        print("    显示时按 KST 换算，于是相机显示比手机快 1 小时。")
        out = cam.marked_run("set-epoch", "date -s @%d" % phone_epoch, seconds=6.0)
        print("    设置输出: %r" % out.strip())

        # ---------- C. hwclock -w（预期失败）----------
        print("\n[C] hwclock -w（预期失败：RTC 写不进去）")
        out = cam.marked_run("hwclock", "hwclock -w; echo ret=$?", seconds=8.0)
        print("    输出:")
        for line in out.strip().split("\n"):
            print("      %s" % line)
        if "timed out" in out or "ret=" in out:
            print("    → 预期内的失败（select() to /dev/rtc ... timed out，/etc/adjtime 只读），")
            print("      说明重启后 RTC 可能仍回报旧时间，需要在开机后重新同步一次。")

        # ---------- D. 复读校验 ----------
        print("\n[D] 复读校验")
        readback = cam.marked_run("readback", "date '+%Y-%m-%d %H:%M:%S'").strip()
        after_epoch, _ = cam.cam_epoch()
        print("    相机读回: %s" % readback)
        print("    手机时间: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print("    相机 epoch=%s (%s)" % (after_epoch, human(after_epoch) if after_epoch else "?"))

        # ---------- E. 人类可读结论 ----------
        print("\n[E] 结论")
        print("    - 用 @epoch 设置后，相机显示的墙上时间会比 UTC+8 的手机快约 1 小时。")
        print("      若要让相机屏幕显示 == 手机显示，应改用：")
        print('          date -s "YYYY-MM-DD HH:MM:SS"')
        print("      即把手机当前时间字符串直接写进去（相机按本地时区 KST 解释），")
        print("      读回来就是同一个数字。")
        print("    - 提示符匹配用的是行尾正则 r'(\\[[^\\]]*\\]\\s*)?#\\s*$'，")
        print("      能同时匹配首个 '[root@drime5 ~]# ' 与后续 '[root@drime5 ~]#'（无尾随空格）。")
        print("    - hwclock -w 失败属预期，RTC 不保存；时间同步应在每次连接时重做。")
        print("    - 相机日期命令是 GNU coreutils 6.9（不是 busybox），支持 date -s \"STRING\"。")
        print("    - 相机上没有 timeout 命令，脚本里不要用。")
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
