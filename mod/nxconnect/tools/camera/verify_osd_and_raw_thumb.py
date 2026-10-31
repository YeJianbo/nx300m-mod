"""验证 e 组 OSD 与 F 组 RAW 缩略图的可行性。

两件事：
  1. **RAW 缩略图**：thumb-cgi 的扩展名白名单不含 srw，所以走 CGI 必失败。
     但相机端有 ImageMagick 的 `convert`，它**能读 RAW**。
     思路：让 convert 读 srw、缩到指定尺寸、写成 jpg 到 SD 卡，
     再用 80 端口的 HTTP 把那个 jpg 取回来。
     本脚本验证：convert 是否存在、能否读 srw、要多久、产物多大。

  2. **OSD**：/api/v1/osd/get 是 POST 带 120 块哈希的增量接口。
     先测一次"空哈希"（等价于全量请求）看返回多少字节、解析出的帧是什么样。
     这决定 E 组能不能做成实时镜像。

产出全部走 /mnt/mmc/，再用 FTP 取回（FTP 根就是 SD 卡）。
"""
import json
import socket
import subprocess
import sys
import time
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
DAEMON = 80
CGI = 8080
IAC = 255
import re
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)


class Shell:
    """可复用相机 shell（IAC 过滤 + 行尾正则提示符）"""

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

    def run(self, cmd, ms=15000):
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
        return "\n".join(l for l in txt.replace("\r", "").splitlines()
                         if l.strip() and l.strip() != cmd.strip()).strip()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def ftp_get(remote, local):
    r = subprocess.run(["curl.exe", "-s", "--user", "root:", "-o", local,
                        "-w", "%{http_code} %{size_download}",
                        "ftp://%s/%s" % (IP, remote)],
                       capture_output=True, text=True, errors="replace")
    return (r.stdout or "").strip()


def http_get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def main():
    sh = Shell(IP)
    sh.login()
    print("=== 已进相机 shell ===\n")

    # ---------- F 组：RAW 缩略图 ----------
    print("########## F 组：RAW 缩略图可行性 ##########")
    print("\n-- 1) convert 是否存在、是不是 ImageMagick --")
    print(sh.run("which convert; convert -version 2>&1 | head -3"))

    print("\n-- 2) 找一个真实的 srw 文件 --")
    srw = sh.run("ls -S /mnt/mmc/DCIM/100PHOTO/*.SRW 2>/dev/null | head -1")
    print("  最大的 SRW: %s" % srw)
    if not srw:
        srw = sh.run("ls /mnt/mmc/DCIM/*/*.SRW 2>/dev/null | head -1")
        print("  退而取第一个: %s" % srw)

    if srw:
        print("\n-- 3) 用 convert 把 srw 转成 320 宽的 jpg（计时）--")
        out = sh.run(
            "cd /mnt/mmc && time convert '%s' -resize 320x /mnt/mmc/_srwtest.jpg 2>&1; "
            "ls -l /mnt/mmc/_srwtest.jpg 2>&1" % srw, ms=60000)
        print("  %s" % out)

        print("\n-- 4) 通过 80 端口 HTTP 取回这个 jpg（App 实际会走的路）--")
        st, body = http_get("http://%s/_srwtest.jpg" % IP, timeout=15)
        print("  HTTP 状态=%s  长度=%s  开头=%s" % (st, len(body), body[:4].hex() if st == 200 else body[:80]))
        if st == 200 and body[:2] == b"\xff\xd8":
            print("  ✅ 拿到真 JPEG —— RAW 缩略图这条路可行！")
        else:
            print("  ❌ 不是 JPEG")

        print("\n-- 5) 对比：直接请求 thumb-cgi 拿 srw（预期失败）--")
        st2, body2 = http_get("http://%s:%d/cgi-bin/thumb?f=%s&w=320&q=80"
                              % (IP, CGI, srw.split("/")[-1]), timeout=15)
        print("  HTTP 状态=%s  长度=%s  内容=%r" % (st2, len(body2), body2[:60]))
    else:
        print("  ⚠️ 没找到 SRW 文件，跳过")

    # ---------- e 组：OSD ----------
    print("\n########## E 组：OSD 可行性 ##########")
    print("\n-- 1) 取景是否可用（OSD 通常要先开着取景）--")
    st, body = http_get("http://%s/api/v1/liveview/get_low" % IP, timeout=10)
    print("  liveview/get_low: 状态=%s 长度=%s" % (st, len(body) if st == 200 else body[:60]))

    print("\n-- 2) OSD 全量请求（POST 空哈希，120 块置 0）--")
    import urllib.parse
    hashs = "00000000" * 120
    data = urllib.parse.urlencode({"hashs": hashs}).encode()
    req = urllib.request.Request("http://%s/api/v1/osd/get" % IP, data=data, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        dt = (time.time() - t0) * 1000
        print("  状态=%s  返回 %d 字节  耗时 %.0f ms" % (r.status, len(raw), dt))
        if len(raw) >= 8:
            def u16(i):
                return (raw[i] & 0xff) << 8 | (raw[i + 1] & 0xff)
            print("  帧宽=%d 帧高=%d 块宽=%d 块高=%d"
                  % (u16(0), u16(2), u16(4), u16(6)))
            print("  前 32 字节: %s" % raw[:32].hex())
            print("  ✅ OSD 有数据返回 —— E 组可行，进一步要测帧率与鼠标响应")
        else:
            print("  ⚠️ 返回为空或过短：%r" % raw[:60])
            print("     可能原因：取景没开 / 相机在播放模式 / mod 版本不支持 OSD")
    except Exception as e:
        print("  ❌ OSD 请求失败：%s" % e)
        print("     注意这个接口是 POST，用 GET 会 404 —— 这是个常见误判点")

    print("\n-- 3) 顺带看 daemon 是否把 OSD 相关路由注册了（源码层面确认）--")
    print(sh.run("strings /opt/usr/apps/nx-remote-controller-mod/nx-remote-controller-daemon 2>/dev/null | "
                 "grep -i 'osd\\|lcd\\|led' | head -12"))

    sh.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
