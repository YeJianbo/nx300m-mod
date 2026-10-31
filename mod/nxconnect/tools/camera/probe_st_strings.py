import os
"""挖出 st 命令里 capdtm 真正支持的所有参数名。

已知：`getusr ADJUSTSHUTTERTYPE` 成功，但另外 12 个我猜的名字全部
`Invalid argument[2]=xxx`。说明参数名来自一张内表，必须从二进制里取。

产出：把 st 二进制的字符串全部 dump 到 SD 卡，用 FTP 取回，在本地筛选。
这样一次取回就能反复分析，不用反复上网。
"""
import re
import socket
import subprocess
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)


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

    def send(self, cmd, wait=1.2):
        self.buf = ""
        self.s.sendall((cmd + "\n").encode())
        time.sleep(wait)
        # 尽量把缓冲抽干
        for _ in range(12):
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
    print("=== 导出 st 的字符串表到 SD 卡 ===\n")

    print("1) 找 st 的位置")
    print(sh.send("which st; ls -l /usr/bin/st 2>&1 | head -2"))

    print("\n2) dump 字符串（strings 可能不存在，用 tr 兜底）")
    # busybox 一般有 strings；没有就退回 sed 过滤可打印字符
    print(sh.send("(strings /usr/bin/st > /mnt/mmc/st_strings.txt 2>/dev/null) || "
                  "(tr -c '[:print:]' '\\n' < /usr/bin/st > /mnt/mmc/st_strings.txt); "
                  "ls -l /mnt/mmc/st_strings.txt", wait=4))

    print("\n3) 取回")
    print("   " + ftp_get("/st_strings.txt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "st_strings.txt")))

    sh.close()

    # ---------- 本地分析 ----------
    print("\n=== 本地分析 st_strings.txt ===")
    try:
        txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "st_strings.txt"), encoding="utf-8", errors="replace").read()
    except Exception as e:
        print("  读取失败: %s" % e)
        return
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    print("  共 %d 行" % len(lines))

    def find(pat, label, limit=60):
        hits = sorted({l for l in lines if re.search(pat, l, re.I)})
        print("\n  --- %s（%d 条）---" % (label, len(hits)))
        for h in hits[:limit]:
            print("      %s" % h[:110])

    find(r"^ADJUST", "以 ADJUST 开头（capdtm 参数名候选）")
    find(r"USERDATA", "含 USERDATA")
    find(r"^set|setusr", "含 set/setusr")
    find(r"capdtm", "含 capdtm")
    find(r"^st cap|usage", "usage 行")
    find(r"^MF_|^WB_|^ISO", "其它参数名风格")
    find(r"mcb|MCB", "MCB 相关")

    print("\n  完成。把上面 ADJUST*/USERDATA 的清单拿来做参数名表。")


if __name__ == "__main__":
    main()
