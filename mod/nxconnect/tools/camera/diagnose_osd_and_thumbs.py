"""诊断：相机当前模式 + 缩略图生成器是谁 + OSD 为什么空。

上一轮的发现：
  - `convert` 在相机上**不存在**（之前"相机端用 ImageMagick 生成缩略图"的说法是错的）
  - /api/v1/osd/get 与 /api/v1/liveview/get_low 都返回 200 但**0 字节**

这两个都要查清楚。写一个 shell 脚本文件到相机上执行（避免 here-string 被 PowerShell 拆行，
之前那样已经把命令打乱了）。
"""
import re
import socket
import subprocess
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

# 要跑的诊断，逐条独立（不依赖 shell 脚本文件，避免引号地狱）
CMDS = [
    # 1. 相机当前状态
    ("camera status",
     "curl -s http://127.0.0.1/api/v1/camera/status"),
    # 2. 取景是否是"模式不对"才空
    ("liveview 长度",
     "curl -s http://127.0.0.1/api/v1/liveview/get_low | wc -c"),
    # 3. thumb-cgi 是谁（busybox httpd 跑什么脚本）
    ("thumb-cgi 文件",
     "ls -l /opt/usr/nx-ks/nx-rc/capdtm/www/cgi-bin/ 2>&1 | head -20"),
    # 4. thumb-cgi 内容（看它调什么命令）
    ("thumb-cgi 内容",
     "head -40 /opt/usr/nx-ks/nx-rc/capdtm/www/cgi-bin/thumb* 2>&1"),
    # 5. 有哪些图像工具
    ("图像工具搜索",
     "for c in convert magick dcraw ufraw exiftool jpegtran djpeg cjpeg ffmpeg; do "
     "p=$(which $c 2>/dev/null); [ -n \"$p\" ] && echo \"$c -> $p\"; done; echo ---; "
     "ls /opt/usr/nx-ks/nx-rc/capdtm/ 2>&1 | head"),
    # 6. OSD 路由是否在 daemon 里（用 grep -a 直接扫二进制）
    ("daemon 里的路由",
     "grep -ao '/api/v1/[a-z_/]*' /opt/usr/apps/nx-remote-controller-mod/nx-remote-controller-daemon "
     "2>/dev/null | sort -u"),
    # 7. daemon 进程状态
    ("daemon 进程",
     "ps | grep -i remote-controller | grep -v grep"),
    # 8. thumb-cgi 对 JPG 是否正常（对照）
    ("thumb JPG 对照",
     "curl -s 'http://127.0.0.1:8080/cgi-bin/thumb?f=SAM_4384.JPG&w=320&q=80' | wc -c"),
]


class Shell:
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 23), timeout=6)
        self.s.settimeout(0.6)
        self.buf = ""

    def _pump(self):
        try:
            d = self.s.recv(4096)
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
        self.buf = ""
        self.s.sendall((cmd + "\n").encode())
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if PROMPT.search(self.buf):
                break
            if not self._pump():
                break
        txt = self.buf
        m = PROMPT.search(txt)
        if m:
            txt = txt[:m.start()]
        txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", txt)
        lines = [l for l in txt.replace("\r", "").splitlines() if l.strip()]
        # 去掉回显行
        return "\n".join(l for l in lines if l.strip() != cmd.strip()).strip()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    sh = Shell(IP)
    sh.login()
    print("=== 相机诊断 ===\n")
    for title, cmd in CMDS:
        print("---------- %s ----------" % title)
        out = sh.run(cmd)
        print(out if out else "  (无输出)")
        print()
    sh.close()


if __name__ == "__main__":
    main()
