#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_time_wallclock.py —— 验证"按墙上时间字符串设置"能让相机显示时间等于手机时间。

== 为什么会有这个问题：时区 vs 墙上时间 ==
    相机时区是 KST (UTC+9)，/etc/localtime 是一个 396 字节的 TZif 文件。
    用户手机在 UTC+8（中国），比 KST 慢 1 小时。

    如果按 epoch 设置：
        date -s @1700000000
      内核只认这个绝对时刻，显示时会用相机本地时区 KST 去换算，
      于是相机屏幕上显示的时间 = 该 epoch 在 UTC+9 的墙上时间，
      比用户手机（UTC+8）快整整 1 小时。

    如果按墙上时间字符串设置：
        date -s "2026-02-17 14:30:00"
      相机直接把年月日时分秒当成"本地时间"写进去（按 KST 解释），
      之后再读出来显示的就是同一个字符串 —— 相机显示的墙上时间 == 传入字符串。
      时区标签仍然是 KST（date '+%z' 还是 +0900），
      但"显示的数字"已经和手机一致了，这正是用户看到的、也是我们想要的。

    所以：想要"相机屏幕显示 == 手机显示"，就必须用 date -s "字符串"，
    不能用 date -s @epoch。

== 另外两个真机事实 ==
    - /bin/date 是 GNU coreutils 6.9（不是 busybox），支持 date -s "STRING"
    - 相机时钟实测停在 2015-02-17（偏差约 11.6 年）
    - hwclock -w 会失败：select() to /dev/rtc to wait for clock tick timed out，
      且 /etc/adjtime 只读 —— 所以 RTC 写不进去，重启后可能回到旧时间。

用法：
    python camera_time_wallclock.py 192.168.5.39
"""

import datetime
import re
import socket
import sys
import time

IAC = 255
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# 提示符必须用行尾正则：后续提示符 "[root@drime5 ~]#" 没有尾随空格
PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

TZ_EPOCH_DIFF = 3600     # 预期：相机 epoch 与手机 epoch 差 3600 秒（时区标签仍是 KST）


class Cam(object):
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
        i, n = 0, len(chunk)
        while i < n:
            b = chunk[i]
            if b == IAC:
                if i + 1 >= n:
                    break
                cmd = chunk[i + 1]
                i += 3 if 0xFB <= cmd <= 0xFE else 2   # IAC 过滤：协商段 3 字节
                continue
            out.append(b)
            i += 1
        text = ANSI_RE.sub("", out.decode("utf-8", "replace"))
        self.buf += text.replace("\r\n", "\n").replace("\r", "\n")

    def wait(self, seconds):
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

    def run(self, cmd, seconds=4.0):
        self.buf = ""
        self.sock.sendall((cmd + "\n").encode("utf-8", "replace"))
        out = self.wait(seconds)
        lines = out.split("\n")
        if lines and lines[0].strip().endswith(cmd.strip()):
            lines = lines[1:]          # 去掉命令回显
        return "\n".join(lines).strip()

    def login(self):
        self.wait(1.5)
        self.sock.sendall(b"root\n")
        self.wait(1.0)
        self.sock.sendall(b"\n")
        self.wait(1.0)
        self.buf = ""
        return self

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def phone_now_string():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def phone_epoch():
    return int(time.time())


def cam_epoch(cam):
    raw = cam.run("date '+%s'")
    m = re.search(r"(\d{9,})", raw)
    return int(m.group(1)) if m else None


def main(argv):
    ip = argv[1] if len(argv) > 1 else "192.168.5.39"
    cam = Cam(ip)
    try:
        cam.login()

        # ---------------- 1) 同步前：相机 vs 手机 ----------------
        print("=== 1) 同步前时间对比 ===")
        before_cam_str = cam.run("date '+%Y-%m-%d %H:%M:%S'")
        before_cam_epoch = cam_epoch(cam)
        print("相机时间 : %s" % before_cam_str)
        print("相机 epoch: %s" % before_cam_epoch)
        print("手机时间 : %s" % phone_now_string())
        print("手机 epoch: %s" % phone_epoch())
        if before_cam_epoch is not None:
            drift = phone_epoch() - before_cam_epoch
            print("偏差     : %d 秒 (约 %.2f 年)" % (drift, drift / 31557600.0))

        # ---------------- 2) 时区配置 ----------------
        print("\n=== 2) 时区配置 ===")
        print("date '+%%Z %%z'      -> %s" % cam.run("date '+%Z %z'"))
        print("ls -l /etc/localtime -> %s" % cam.run("ls -l /etc/localtime"))
        print("localtime 大小        -> %s 字节" % cam.run("wc -c < /etc/localtime").strip())
        print("Asia/Shanghai        -> %s" % cam.run("ls -l /usr/share/zoneinfo/Asia/Shanghai"))
        print("GNU date 版本         -> %s" % cam.run("date --version | head -n 1"))

        # ---------------- 3) 按墙上时间字符串设置 ----------------
        target = phone_now_string()
        print("\n=== 3) 用 date -s \"%s\" 设置（墙上时间，不是 @epoch）===" % target)
        out = cam.run('date -s "%s"' % target)
        print("设置输出: %s" % (out if out.strip() else "(无输出，GNU date 设置成功通常静默)"))
        time.sleep(0.3)

        # ---------------- 4) 复读校验 ----------------
        print("\n=== 4) 复读校验 ===")
        readback = cam.run("date '+%Y-%m-%d %H:%M:%S'").strip()
        print("目标字符串: %s" % target)
        print("相机读回  : %s" % readback)
        wall_ok = (readback == target)
        print("墙上时间一致: %s" % ("✅ 是" if wall_ok else "❌ 否"))
        if not wall_ok:
            # 允许 1 秒误差（设置和复读之间有耗时）
            try:
                a = datetime.datetime.strptime(target, "%Y-%m-%d %H:%M:%S")
                b = datetime.datetime.strptime(readback, "%Y-%m-%d %H:%M:%S")
                print("  字符串差异 = %d 秒" % abs((b - a).total_seconds()))
            except ValueError:
                print("  读回值不是预期的日期格式，可能设置失败")

        # ---------------- 5) epoch 差（预期 3600）----------------
        print("\n=== 5) epoch 视角（解释为什么这里仍有 1 小时差）===")
        after_cam_epoch = cam_epoch(cam)
        ph_epoch = phone_epoch()
        print("相机 epoch: %s" % after_cam_epoch)
        print("手机 epoch: %s" % ph_epoch)
        if after_cam_epoch is not None:
            diff = after_cam_epoch - ph_epoch
            print("差值      : %+d 秒" % diff)
            if abs(diff - TZ_EPOCH_DIFF) <= 2:
                print("✅ 差值约 +3600 秒，符合预期：相机把字符串按 KST(UTC+9) 解释，")
                print("   而手机是 UTC+8 —— 时区标签仍是 KST，但显示的数字已经和手机一致。")
            elif abs(diff) <= 2:
                print("ℹ️ 差值约 0：说明相机时区与手机一致，或者 date 已按其他方式解释。")
            else:
                print("⚠️ 差值不在预期范围，请检查相机时区设置。")

        print("\n结论：判据是「复读的墙上时间字符串 == 手机时间字符串」，")
        print("     而不是「epoch 相等」。epoch 相差 3600 秒是时区标签差异，属正常。")
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
