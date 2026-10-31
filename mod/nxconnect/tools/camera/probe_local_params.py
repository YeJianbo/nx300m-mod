"""关键问题：能否在**本地**读到相机的当前曝光参数？

这决定"手机端自备 OSD UI"这个方案的可行性上限：
  - 若**读不到**当前值 → 本地 UI 只能发按键，无法显示"现在是 ISO 800 / F2.0"
    （我之前已确认 daemon 没有参数接口、capdtm 只能读 1 个参数）
  - 若**读得到** → 本地 UI 可做成完整的参数控制面板，不再依赖 OSD 镜像

已知线索：相机自己的 OSD 会显示 `EV : 0.0`、`1/100`、`F2.0`、`AUTO 100`、`AWB`……
这些字串必然由相机端某处产生。去找它们的来源。

排查方向：
  1. vconf（Tizen 的键值配置系统）—— 相机 UI 常用它存状态
  2. 相机 App 的共享内存 / 内存映射文件（mod 的 wifi.sh 就用了
     /tmp/var/run/memory/ap_setting/request_type 这种路径）
  3. 相机端日志
  4. 有没有可用的 PTP/CSV 之类接口

输出一律写 SD 卡再 FTP 取回（FTP 根就是 /mnt/mmc，HTTP 80 取不到）。
"""
import re
import socket
import subprocess
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

CMDS = [
    ("vconf 工具是否存在", "which vconf; ls -l /usr/bin/vconf 2>&1 | head -2"),
    ("vconf 全部键（找曝光相关）",
     "vconf -l 2>/dev/null | head -80"),
    ("vconf 里含 memory 的键",
     "vconf -l 2>/dev/null | grep -i 'memor\\|ap_setting\\|capture\\|camera' | head -40"),
    ("/tmp/var/run/memory 下有什么",
     "ls -la /tmp/var/run/memory/ 2>&1 | head -40"),
    ("ap_setting 目录内容",
     "ls -la /tmp/var/run/memory/ap_setting/ 2>&1 | head -20"),
    ("有没有其它 mmap 状态目录",
     "find /tmp/var/run -maxdepth 3 -type d 2>/dev/null | head -40"),
    ("相机相关进程",
     "ps aux 2>/dev/null | grep -iE 'capture|camera|dsp|mcb' | grep -v grep | head -20"),
    ("相机端日志目录",
     "ls -la /var/log/ 2>&1 | head -25"),
    ("含曝光字样的文件（限 /var/log 与 /tmp）",
     "grep -rl 'ADJUSTISOVALUE\\|ADJUSTEVALUE\\|EVValue' /var/log /tmp 2>/dev/null | head -10"),
    ("有没有 ptp/csv 接口二进制",
     "ls /usr/bin/ 2>/dev/null | grep -iE 'ptp|csv|mcb|capture' | head -20"),
]


class Shell:
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 23), timeout=6)
        self.s.settimeout(0.6)
        self.buf = ""

    def _pump(self):
        try:
            d = self.s.recv(8192)
        except socket.timeout:
            return True
        except Exception:
            return False
        if not d:
            return False
        i, n = 0, len(d)
        while i < n:
            b = d[i]
            if b == IAC:
                if i + 1 >= n:
                    break
                i += 3 if 0xFB <= d[i + 1] <= 0xFE else 2
                continue
            self.buf += chr(b)
            i += 1
        return True

    def await_text(self, t, ms):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if t in self.buf:
                return True
            if not self._pump():
                return False
        return t in self.buf

    def login(self):
        if not self.await_text("login:", 8000):
            raise RuntimeError("没看到 login")
        self.buf = ""
        self.s.sendall(b"root\n")
        if not self.await_text("#", 8000):
            raise RuntimeError("没进 shell")
        self.buf = ""

    def run(self, cmd, ms=20000):
        """输出重定向到文件，避免 telnet 回显把内容打乱"""
        self.buf = ""
        self.s.sendall(("%s >/mnt/mmc/q.txt 2>&1\n" % cmd).encode())
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if PROMPT.search(self.buf):
                break
            if not self._pump():
                break
        time.sleep(0.3)
        r = subprocess.run(["curl.exe", "-s", "--user", "root:", "ftp://%s/q.txt" % IP],
                           capture_output=True, text=True, errors="replace")
        return (r.stdout or "").strip()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    sh = Shell(IP)
    sh.login()
    print("=== 排查：能否本地读取曝光参数 ===\n")
    for title, cmd in CMDS:
        print("---------- %s ----------" % title)
        out = sh.run(cmd)
        if out:
            for line in out.splitlines()[:30]:
                print("  %s" % line)
        else:
            print("  (无输出)")
        print()
    sh.run("rm -f /mnt/mmc/q.txt")
    sh.close()


if __name__ == "__main__":
    main()
