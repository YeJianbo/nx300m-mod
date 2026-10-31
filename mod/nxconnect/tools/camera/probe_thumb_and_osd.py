"""把相机端 thumb CGI 脚本取回来分析，并确认 daemon 的路由表。

为什么这么做：
  - 之前以为缩略图由 ImageMagick 的 `convert` 生成，实测**相机上没有 convert**，
    只有 djpeg/cjpeg。所以必须看 thumb 脚本源码才知道它到底用什么。
  - OSD 返回 0 字节，要确认 daemon 二进制里到底有没有注册 /api/v1/osd/get。

取回方式：8080 的 httpd 根是 /opt/usr/nx-ks/nx-rc/capdtm/www，
而 CGI 目录就在其下，所以可以直接用 HTTP 把脚本自身当文件取（httpd 对 .sh 会当文本返回）。
拿不到就退回 FTP（FTP 根是 SD 卡，够不到 /opt，所以优先 HTTP）。
"""
import re
import socket
import sys
import time
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)


def http(url, timeout=10, data=None, method=None):
    try:
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()


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
        """用唯一标记把输出夹起来，避免回显干扰"""
        token = "NXZ%d" % (int(time.time() * 1000) % 1000000)
        begin, end = "<<%sB>>" % token, "<<%sE>>" % token
        self.buf = ""
        self.s.sendall(("echo %s\n" % begin).encode())
        self.s.sendall((cmd + "\n").encode())
        self.s.sendall(("echo %s\n" % end).encode())
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            if end in self.buf:
                break
            if not self._pump():
                break
        txt = self.buf
        bi, ei = txt.find(begin), txt.rfind(end)
        if bi >= 0 and ei > bi:
            txt = txt[bi + len(begin):ei]
        txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", txt)
        lines = [l.rstrip() for l in txt.replace("\r", "").splitlines()]
        # 丢掉命令回显行与标记残行
        out = [l for l in lines if l.strip() and not l.strip().startswith("echo ")
               and "NXZ" not in l and l.strip() != cmd.strip()]
        return "\n".join(out).strip()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    print("########## 1) 相机当前状态（HTTP，不经 shell）##########")
    st, body = http("http://%s/api/v1/camera/status" % IP)
    print("  状态=%s" % st)
    print("  %s" % body.decode("utf-8", "replace")[:400])

    print("\n########## 2) 取景两个接口的真实长度 ##########")
    for ep in ("/api/v1/liveview/get", "/api/v1/liveview/get_low"):
        st, body = http("http://%s%s" % (IP, ep), timeout=12)
        print("  %-28s 状态=%s 长度=%s" % (ep, st, len(body) if st == 200 else body[:60]))

    print("\n########## 3) 把 thumb 脚本取回来（HTTP 直读源码）##########")
    sh = Shell(IP)
    sh.login()
    # httpd 根
    print("  httpd 根: %s" % sh.run("grep -a '\\-h' /opt/usr/nx-ks/nx-rc/capdtm/capdtm-httpd.sh | head -3"))
    print("  CGI 目录: %s" % sh.run("ls /opt/usr/nx-ks/nx-rc/capdtm/www/cgi-bin/"))
    print("\n  --- thumb 脚本全文 ---")
    print(sh.run("cat /opt/usr/nx-ks/nx-rc/capdtm/www/cgi-bin/thumb", ms=25000))

    print("\n########## 4) daemon 二进制里的路由表 ##########")
    print("  daemon 路径候选:")
    print(sh.run("ls -l /opt/usr/apps/nx-remote-controller-mod/ 2>&1 | head -20"))
    print("\n  路由字符串:")
    print(sh.run("for p in /opt/usr/apps/nx-remote-controller-mod/nx-remote-controller-daemon "
                 "/opt/usr/nx-ks/nx-rc/tools/usr/bin/nx-remote-controller-daemon; do "
                 "[ -f $p ] && echo \"== $p\" && grep -ao '/api/v1/[a-z_/]*' $p | sort -u; done"))

    print("\n########## 5) OSD 再试一次（POST，明确 Content-Type）##########")
    import urllib.parse
    data = urllib.parse.urlencode({"hashs": "00000000" * 120}).encode()
    st, body = http("http://%s/api/v1/osd/get" % IP, timeout=20, data=data, method="POST")
    print("  状态=%s 长度=%s 内容前 64 字节=%s" % (st, len(body) if body else 0, body[:64]))

    print("\n########## 6) OSD 是否需要先开取景 ##########")
    print("  尝试启动取景再问一次 OSD:")
    st, _ = http("http://%s/api/v1/lcd/on" % IP)
    print("    lcd/on -> %s" % st)
    time.sleep(2)
    st, body = http("http://%s/api/v1/liveview/get_low" % IP, timeout=12)
    print("    取景长度 -> %s (%s)" % (len(body) if st == 200 else body[:40], st))
    st, body = http("http://%s/api/v1/osd/get" % IP, timeout=20, data=data, method="POST")
    print("    OSD 长度 -> %s (%s)" % (len(body) if body else 0, st))

    sh.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
