"""调试"把输出写文件再取回"这条路 —— 前两次都拿到空内容。

排查顺序：
  1. shell 里 echo 一个已知字符串到 /mnt/mmc/h.txt，看文件是否存在、多大
  2. 分别用 FTP 与 HTTP(80端口) 两种方式取，看哪种能取到
  3. 确认 /mnt/mmc 与 FTP 根到底是不是同一个地方
  4. 顺便确认 st 命令到底有没有输出（可能 st 需要 tty 才输出）

关键修正：HTTP 的 80 端口根目录是 SD 卡（实测能取 /_srwtest.jpg 这类），
比 FTP 更直接，优先用 HTTP。
"""
import re
import socket
import sys
import time
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)


def http(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


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

    def raw(self, cmd, ms=8000):
        """发一条命令，收集原始输出（含回显），用于诊断"""
        self.buf = ""
        self.s.sendall((cmd + "\n").encode())
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if PROMPT.search(self.buf):
                break
            if not self._pump():
                break
        txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", self.buf)
        m = PROMPT.search(txt)
        if m:
            txt = txt[:m.start()]
        return txt.replace("\r", "")

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    sh = Shell(IP)
    sh.login()
    print("=== 1) 写一个已知内容到 /mnt/mmc/h.txt，并 ls 确认 ===")
    out = sh.raw("echo HELLO_NX_12345 > /mnt/mmc/h.txt; ls -l /mnt/mmc/h.txt; "
                 "cat /mnt/mmc/h.txt")
    print(out)
    print()

    print("=== 2) 直接 cat（不重定向），确认 st 是否有输出 ===")
    out = sh.raw("st cap capdtm getusr ADJUSTSHUTTERTYPE; echo RC=$?", ms=10000)
    print(out)
    print()

    print("=== 3) st 是否有参数才会输出（试 st 无参 / st cap 无参）===")
    out = sh.raw("st 2>&1 | head -5; echo '---rc='$?", ms=10000)
    print(out)
    print()

    print("=== 4) 两种取回方式对比 ===")
    st_http, body_http = http("http://%s/h.txt" % IP)
    print("  HTTP  http://%s/h.txt -> 状态=%s 内容=%r" % (IP, st_http, body_http[:120]))
    import subprocess
    r = subprocess.run(["curl.exe", "-s", "--user", "root:",
                        "ftp://%s/h.txt" % IP],
                       capture_output=True, text=True, errors="replace")
    print("  FTP   ftp://%s/h.txt -> 内容=%r  stderr=%r" % (IP, (r.stdout or "")[:120], (r.stderr or "")[:120]))
    print()

    print("=== 5) FTP 根目录到底是什么（列一下）===")
    r = subprocess.run(["curl.exe", "-s", "--user", "root:", "ftp://%s/" % IP],
                       capture_output=True, text=True, errors="replace")
    print("  FTP / 列表前 300 字符:\n%s" % (r.stdout or "")[:300])
    out = sh.raw("df -h | head -8; echo ---; mount | grep -i mmc | head -5")
    print("  相机挂载点:\n%s" % out)
    print()

    print("=== 6) 把 st 输出重定向到文件，再分别取 ===")
    out = sh.raw("st cap capdtm getusr ADJUSTSHUTTERTYPE > /mnt/mmc/st.txt 2>&1; "
                 "echo RC=$?; ls -l /mnt/mmc/st.txt; echo '--- 内容:'; cat /mnt/mmc/st.txt",
                 ms=12000)
    print(out)
    st_http, body_http = http("http://%s/st.txt" % IP)
    print("\n  HTTP 取 st.txt -> 状态=%s 内容=%r" % (st_http, body_http[:200]))

    sh.close()


if __name__ == "__main__":
    main()
