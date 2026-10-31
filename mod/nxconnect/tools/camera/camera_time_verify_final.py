#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_time_verify_final.py —— 最终验证：墙上时间同步 + 正则提示符匹配，两者一起验。

这个脚本用的是与 Kotlin 侧（Recon 的 NxShell）完全一致的协议：
    - 设置时间：date -s "<YYYY-MM-DD HH:MM:SS>"       （墙上时间字符串，不用 @epoch）
    - 提示符  ：PROMPT_AT_END = re.compile(r"(\\[[^\\]]*\\]\\s*)?#\\s*$", re.M)

为什么要专门用 PROMPT_AT_END：
    相机后续提示符是 "[root@drime5 ~]#"，尾随空格被内核吞掉了。
    用 "# " in buf 判断会永远等不到；用行尾正则才能在两种提示符
    （首条带空格 / 后续不带空格）下都正确切分。

要断言的两件事：
    A. 输出里不残留提示符 —— 提取到的正文不能含 '#'，也不能含 'root@'
    B. 手机时间与相机读回时间"分钟级一致"

打印每项的 ✅ / ❌。

用法：
    python camera_time_verify_final.py 192.168.5.39
"""

import datetime
import re
import socket
import sys
import time

# ---- 与 Kotlin 侧实现逐字一致的提示符正则 ----
PROMPT_AT_END = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

IAC = 255
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class CamShell(object):
    """IAC 过滤 + 按行缓冲 + 行尾正则提示符匹配。"""

    def __init__(self, ip, port=23, timeout=0.6):
        self.sock = socket.create_connection((ip, port), 5.0)
        self.sock.settimeout(timeout)
        self.buf = ""

    # ------------------------------------------------------------ IAC 过滤
    def _pump(self):
        """收一次数据：IAC 协商整段吞掉，其余字节按 utf-8 解码并追加到行缓冲。

        注意：这里不因为 socket 超时就丢弃 self.buf —— 半行会留在缓冲里，
        等待后续 recv 补齐，否则长输出会被切成碎片，正则也匹配不到完整行。
        """
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return
        except OSError:
            return
        if not chunk:
            return

        out = bytearray()
        i, n = 0, len(chunk)
        while i < n:
            b = chunk[i]
            if b == IAC:                       # 255
                if i + 1 >= n:
                    break                      # 命令字节还在下一次 recv
                cmd = chunk[i + 1]
                if 0xFB <= cmd <= 0xFE:
                    i += 3                     # WILL/WONT/DO/DONT + 选项字节
                elif cmd == IAC:
                    out.append(IAC)            # 转义 0xFF
                    i += 2
                else:
                    i += 2
                continue
            out.append(b)
            i += 1

        text = ANSI_RE.sub("", out.decode("utf-8", "replace"))
        self.buf += text.replace("\r\n", "\n").replace("\r", "\n")

    def read_until_prompt(self, seconds=6.0):
        """读到提示符为止，返回不含提示符的正文。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._pump()
            m = PROMPT_AT_END.search(self.buf)
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
        self.buf = ""
        self.send(cmd + "\n")
        body = self.read_until_prompt(seconds)
        # 去掉命令自身的回显行（相机开了 echo）
        lines = body.split("\n")
        if lines and lines[0].strip().endswith(cmd.strip()):
            lines = lines[1:]
        return "\n".join(lines).strip("\n")

    def login(self):
        self.read_until_prompt(1.5)     # banner + IAC
        self.send("root\n")
        self.read_until_prompt(1.0)
        self.send("\n")
        self.read_until_prompt(1.0)     # 首个提示符
        self.buf = ""
        return self

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def check(label, ok, detail=""):
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  —— " + detail) if detail else ""))
    return ok


def main(argv):
    ip = argv[1] if len(argv) > 1 else "192.168.5.39"
    results = []
    cam = CamShell(ip)
    try:
        cam.login()
        print("=== 已登录 %s，开始最终验证 ===" % ip)

        # ---------- 1) 同步前 ----------
        before = cam.run("date '+%Y-%m-%d %H:%M:%S'")
        print("\n[1] 同步前相机时间: %r" % before)
        results.append(check("同步前能拿到时间输出", bool(re.search(r"\d{4}-\d{2}-\d{2}", before))))

        # ---------- 2) 提示符正则切分是否干净 ----------
        print("\n[2] 提示符正则切分检查")
        results.append(check("输出不残留 '#'", "#" not in before,
                             "残留则说明 PROMPT_AT_END 没匹配到" if "#" in before else ""))
        results.append(check("输出不残留 'root@'", "root@" not in before))
        results.append(check("输出不含 ANSI 转义残留", "\x1b" not in before))

        # ---------- 3) 墙上时间设置 ----------
        target = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("\n[3] date -s \"%s\"（墙上时间字符串）" % target)
        set_out = cam.run('date -s "%s"' % target)
        print("    设置命令输出: %r" % set_out)
        results.append(check("设置输出不残留提示符", "#" not in set_out and "root@" not in set_out))
        time.sleep(0.3)

        # ---------- 4) 复读校验 ----------
        readback = cam.run("date '+%Y-%m-%d %H:%M:%S'").strip()
        print("\n[4] 复读: %r  (目标: %r)" % (readback, target))
        results.append(check("复读输出不残留提示符", "#" not in readback and "root@" not in readback))

        wall_ok = (readback == target)
        if not wall_ok:
            try:
                a = datetime.datetime.strptime(target, "%Y-%m-%d %H:%M:%S")
                b = datetime.datetime.strptime(readback, "%Y-%m-%d %H:%M:%S")
                delta = abs((b - a).total_seconds())
                wall_ok = delta <= 2
                print("    字符串差 %.0f 秒（设置→复读本身的耗时）" % delta)
            except ValueError:
                pass
        results.append(check("墙上时间字符串与目标一致", wall_ok, "相机显示 == 手机时间"))

        # ---------- 5) 分钟级一致 ----------
        print("\n[5] 分钟级一致（忽略秒）")
        try:
            t_target = datetime.datetime.strptime(target, "%Y-%m-%d %H:%M:%S")
            t_read = datetime.datetime.strptime(readback, "%Y-%m-%d %H:%M:%S")
            same_minute = (t_target.strftime("%Y-%m-%d %H:%M") == t_read.strftime("%Y-%m-%d %H:%M"))
            results.append(check("手机时间与相机读回分钟级一致", same_minute,
                                 "%s vs %s" % (t_target.strftime("%Y-%m-%d %H:%M"),
                                               t_read.strftime("%Y-%m-%d %H:%M"))))
        except ValueError:
            results.append(check("手机时间与相机读回分钟级一致", False, "读回值格式不可解析"))

        # ---------- 6) 附带观察：epoch 差值（时区标签）----------
        print("\n[6] 观察项：epoch 差值（时区标签仍是 KST 属正常）")
        cam_epoch_raw = cam.run("date '+%s'")
        m = re.search(r"(\d{9,})", cam_epoch_raw)
        if m:
            diff = int(m.group(1)) - int(time.time())
            print("    相机 epoch - 手机 epoch = %+d 秒" % diff)
            print("    （约 +3600 说明相机按 KST 解释墙上时间字符串，显示数值仍正确）")
        else:
            print("    未能解析相机 epoch: %r" % cam_epoch_raw)

        # ---------- 汇总 ----------
        print("\n=== 汇总: %d/%d 项通过 ===" % (sum(1 for r in results if r), len(results)))
        print("结论: %s" % ("全部通过 ✅ —— 墙上时间同步 + 正则提示符匹配均正常"
                            if all(results) else "存在失败项 ❌，见上文"))
    finally:
        cam.close()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
