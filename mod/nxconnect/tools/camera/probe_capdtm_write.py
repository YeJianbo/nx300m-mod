"""H 组关键一问：capdtm 能否**写入**曝光参数（而不是只读）。

已知（本轮实测）：
  - `st cap capdtm getusr ADJUSTSHUTTERTYPE` -> `UserData is ADJUSTSHUTTERTYPE_MECHA (0x750000)`  ✅ 读得到
  - 而 `st cap lens info` -> `Cann't execute capture command over MCB!`  ❌ 被挡
  => capdtm 与 lens 走的是不同通路，capdtm 是活的。

要回答的问题：
  1. capdtm 有没有 set 命令？（看它自己的 usage）
  2. 有哪些参数名可读？（逐个大写名试）
  3. 能否写入？先读一个当前值，写一个新值，再读回确认。
     **只做「改完能改回去」的测试**，避免把相机留在奇怪状态。

输出一律重定向到文件，用 FTP 取回（HTTP 80 端口**不是** SD 卡根，取不到，
这个也纠正了我之前的假设：FTP 根才是 /mnt/mmc，HTTP 80 根是 DCIM 之类）。
"""
import re
import socket
import subprocess
import sys
import time
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)


def ftp_read(path):
    r = subprocess.run(["curl.exe", "-s", "--user", "root:",
                        "ftp://%s%s" % (IP, path)],
                       capture_output=True, text=True, errors="replace")
    return r.stdout or ""


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

    def run(self, cmd, ms=15000):
        """直接把输出写到文件，命令本身不产生 stdout"""
        self.buf = ""
        self.s.sendall(("%s >/mnt/mmc/h.txt 2>&1\n" % cmd).encode())
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if PROMPT.search(self.buf):
                break
            if not self._pump():
                break
        time.sleep(0.3)
        return ftp_read("/h.txt")

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    sh = Shell(IP)
    sh.login()
    print("=== 相机 shell 已连上 ===\n")

    print("########## 1) capdtm 的用法（确认有没有 set）##########")
    print(sh.run("st cap capdtm 2>&1; echo '===rc='$?"))
    print()

    print("########## 2) capdtm help ##########")
    print(sh.run("st cap capdtm help 2>&1 | head -40; echo '===rc='$?"))
    print()

    print("########## 3) 逐个试参数名，看哪些可读 ##########")
    names = ("ADJUSTSHUTTERTYPE ADJUSTISOVALUE ADJUSTWB ADJUSTMETERING "
             "ADJUSTEV ADJUSTAPERTURE ADJUSTSHUTTERSPEED ADJUSTDRIVE "
             "ADJUSTFLASH ADJUSTFOCUS ADJUSTAFMODE ADJUSTQUALITY ADJUSTSIZE")
    print(sh.run("for p in %s; do printf '%%-24s ' $p; "
                 "st cap capdtm getusr $p 2>&1 | head -1; done" % names))
    print()

    print("########## 4) 是否有 getusr 之外的子命令（关键：set）##########")
    print(sh.run("strings $(which st) 2>/dev/null | grep -iE '^setusr|^getusr|capdtm' | head -20; "
                 "echo '===rc='$?"))
    print()

    print("########## 5) 尝试写入：先读 ISO，设一个值，再读回 ##########")
    print("-- 5.1 当前值 --")
    before = sh.run("st cap capdtm getusr ADJUSTISOVALUE 2>&1")
    print("   %s" % before.strip())
    print("-- 5.2 试 setusr（若命令不存在会报 usage）--")
    print(sh.run("st cap capdtm setusr ADJUSTISOVALUE 800 2>&1; echo '===rc='$?"))
    print("-- 5.3 读回 --")
    after = sh.run("st cap capdtm getusr ADJUSTISOVALUE 2>&1")
    print("   %s" % after.strip())
    print("   判定: %s" % ("值变了 -> capdtm 可写！" if before.strip() != after.strip()
                            else "值没变 -> 只读，或 setusr 不存在"))
    print()

    print("########## 6) 另一条路：st cap capt mcb（原始 MCB 命令）##########")
    print(sh.run("st cap capt mcb 2>&1 | head -20; echo '===rc='$?"))
    print()

    print("########## 7) 对照：当前相机模式（HTTP）##########")
    try:
        with urllib.request.urlopen("http://%s/api/v1/camera/status" % IP, timeout=8) as r:
            print("   %s" % r.read().decode("utf-8", "replace"))
    except Exception as e:
        print("   %s" % e)

    sh.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
