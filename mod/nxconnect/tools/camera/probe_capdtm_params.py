import os
"""找到 st 真正实现所在（capdtm 参数表在里面），并挖出参数名。

已知：/usr/bin/st 只有 4680 字节，是个壳；真正的实现应该在某个 lib 或另一个
可执行文件里。要找出来，才能拿到 capdtm 的合法参数名表。
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

    def send(self, cmd, wait=1.5):
        self.buf = ""
        self.s.sendall((cmd + "\n").encode())
        time.sleep(wait)
        for _ in range(15):
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
    print("=== 1) st 这个壳依赖什么 ===")
    print(sh.send("ldd /usr/bin/st 2>&1; echo '--- head:'; head -c 200 /usr/bin/st | od -c | head -8",
                  wait=3))

    print("\n=== 2) 系统里跟 capdtm/capture 有关的库与程序 ===")
    print(sh.send("ls -l /usr/lib/libcapture* /usr/lib/libnx* /usr/lib/*capdtm* 2>/dev/null; "
                  "echo '--- find:'; find /usr /opt -name '*capdtm*' -o -name '*capture*' 2>/dev/null | head -20",
                  wait=6))

    print("\n=== 3) 把这些库里含 ADJUST/USERDATA 的字符串 dump 出来 ===")
    # 找到候选后统一 dump 到一个文件
    print(sh.send(
        "OUT=/mnt/mmc/capstr.txt; : > $OUT; "
        "for f in $(find /usr/lib /usr/bin /opt -maxdepth 4 \\( -name '*capture*' -o -name '*capdtm*' "
        "-o -name 'libnx*' \\) -type f 2>/dev/null | head -20); do "
        "echo \"### $f\" >> $OUT; "
        "(strings $f 2>/dev/null || tr -c '[:print:]' '\\n' < $f) | grep -E 'ADJUST|USERDATA|setusr|getusr' >> $OUT; "
        "done; wc -l $OUT", wait=20))

    print("\n=== 4) 取回 ===")
    print("   " + ftp_get("/capstr.txt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "capstr.txt")))
    print(sh.send("ls -l /mnt/mmc/capstr.txt", wait=1.5))

    sh.close()

    print("\n=== 本地分析 ===")
    try:
        txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "capstr.txt"), encoding="utf-8", errors="replace").read()
    except Exception as e:
        print("  读取失败 %s" % e)
        return
    if not txt.strip():
        print("  (空)")
        return
    for line in txt.splitlines()[:200]:
        print("  %s" % line[:120])
    print("\n  去重后的 ADJUST* 参数名:")
    names = sorted({m for m in re.findall(r"ADJUST[A-Z0-9_]+", txt)})
    for n in names:
        print("    %s" % n)
    print("  共 %d 个" % len(names))


if __name__ == "__main__":
    main()
