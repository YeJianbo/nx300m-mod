import os
"""最后一个关键探测：capdtm 的合法参数名表，以及能否写入。

上一轮走偏了：从 libcapture-fw-prod.so 挖到的是**图像处理内部函数名**
（ADJUST_OSD_*、ADJUST_YCC），不是 capdtm 的参数名。

正确做法：`ADJUSTSHUTTERTYPE` 是**唯一确认可用**的参数名，
就在同一个二进制里搜它的**邻居**——参数名表通常是连续字符串数组。
所以：搜出所有含 SHUTTERTYPE 的行，以及它前后的字符串。

同时把 H 组的结论钉死：
  - capdtm 能读（至少一个参数）
  - 能否写？（看有没有 setusr 的 usage/字符串）
  - st cap capt mcb 是否可用
"""
import re
import socket
import subprocess
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

TARGETS = [
    "/usr/lib/libcapture-fw-prod.so",
    "/usr/lib/libshell-command.so",
    "/usr/bin/st",
]


def ftp_get(remote, local):
    r = subprocess.run(["curl.exe", "-s", "--user", "root:", "-o", local,
                        "-w", "%{http_code} %{size_download}",
                        "ftp://%s%s" % (IP, remote)],
                       capture_output=True, text=True, errors="replace")
    return (r.stdout or "").strip()


class Shell:
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 23), timeout=6)
        self.s.settimeout(0.5)
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

    def run(self, cmd, wait=6.0):
        self.buf = ""
        self.s.sendall((cmd + "\n").encode())
        time.sleep(wait)
        for _ in range(20):
            if not self._pump():
                break
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", self.buf).replace("\r", "")

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    sh = Shell(IP)
    sh.login()

    print("=== 1) 哪个二进制里含 ADJUSTSHUTTERTYPE ===")
    print(sh.run("for f in %s; do printf '%%-40s ' $f; "
                 "strings $f 2>/dev/null | grep -c ADJUSTSHUTTERTYPE; done"
                 % " ".join(TARGETS), wait=8))

    print("\n=== 2) 搜所有形如 ADJUST<大写> 的标识符（这才是参数名风格）===")
    print(sh.run("for f in %s; do echo \"### $f\"; "
                 "strings $f 2>/dev/null | grep -oE 'ADJUST[A-Z0-9_]{3,}' | sort -u | head -60; done"
                 % " ".join(TARGETS), wait=15))

    print("\n=== 3) 找 setusr / getusr 的字符串与 usage（判断可否写）===")
    print(sh.run("for f in %s; do echo \"### $f\"; "
                 "strings $f 2>/dev/null | grep -iE 'setusr|getusr|capdtm|Invalid argument' | sort -u | head -30; done"
                 % " ".join(TARGETS), wait=12))

    print("\n=== 4) st cap capt mcb 的 usage（原始 MCB 通路）===")
    print(sh.run("st cap capt mcb 2>&1 | head -12; echo '--- rc='$?", wait=6))

    print("\n=== 5) 直接试探：ADJUSTSHUTTERTYPE 邻居式的常见命名 ===")
    # 常见命名：ADJUST + 属性名。一次性试一批，把结果写文件取回
    print(sh.run(
        "OUT=/mnt/mmc/adjust.txt; : > $OUT; "
        "for p in ADJUSTSHUTTERTYPE ADJUSTSHUTTER ADJUSTSHUTTERSPEED ADJUSTEXPOSURE "
        "ADJUSTEXPOSUREMODE ADJUSTDRIVEMODE ADJUSTMODE ADJUSTISO ADJUSTSENSITIVITY "
        "ADJUSTWHITEBALANCE ADJUSTFLASHMODE ADJUSTLENSPROPERTY ADJUSTZOOM "
        "ADJUSTFOCUSMODE ADJUSTFOCUSAREA ADJUSTFRAMERATE ADJUSTMOVIESIZE; do "
        "printf '%-28s ' $p >> $OUT; st cap capdtm getusr $p >> $OUT 2>&1 | head -1; done; "
        "cat $OUT", wait=25))

    print("\n=== 6) 取回结果文件 ===")
    print("   " + ftp_get("/adjust.txt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "adjust.txt")))

    sh.close()

    print("\n=== 本地看结果 ===")
    try:
        print(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "adjust.txt"), encoding="utf-8", errors="replace").read())
    except Exception as e:
        print("  读取失败 %s" % e)


if __name__ == "__main__":
    main()
