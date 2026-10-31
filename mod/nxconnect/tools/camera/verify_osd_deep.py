"""OSD 深入验证：帧率、增量数据量、鼠标注入是否响应。

背景：全量 OSD 请求返回 1,383,276 字节（720x480，80x40 块，2160 块）。
按 osd.js 的协议，客户端要维护 120 个块的哈希……但块数算下来是
(720/80)*(480/40) = 9*12 = 108 块。120 是 mod 硬编码的上限，实际按 108 算。
所以增量请求只送变化的块，数据量应远小于全量。

本脚本要回答三个问题：
  1. 连续 OSD 请求的耗时（决定能不能做成实时镜像）
  2. 用真实哈希做增量请求后，返回多少字节（决定带宽）
  3. **鼠标注入是否真的改变相机屏幕** —— 这是 E 组能不能"操作相机菜单"的关键前提

第 3 点做法：取一帧 OSD 算指纹 → 注入一次鼠标点击（EV 键位置或屏幕中央）
→ 再取一帧 → 比较指纹是否变化。变化即说明鼠标注入生效。
"""
import hashlib
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)

FRAME_W, FRAME_H = 720, 480
BLOCK_W, BLOCK_H = 80, 40
COLS, ROWS = FRAME_W // BLOCK_W, FRAME_H // BLOCK_H
NBLOCKS = COLS * ROWS


def http(url, timeout=20, data=None, method=None):
    try:
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def osd_request(hashes):
    """发一次 OSD 请求；hashes 是长度 NBLOCKS 的整数列表（0=请求该块）"""
    s = "".join("%08x" % h for h in hashes)
    data = urllib.parse.urlencode({"hashs": s}).encode()
    t0 = time.time()
    st, body = http("http://%s/api/v1/osd/get" % IP, timeout=25, data=data, method="POST")
    return st, body, (time.time() - t0) * 1000


def parse_osd(raw):
    """解析 OSD 响应，返回 (frame_w, frame_h, block_w, block_h, {block_index: (x,y,hash,bytes)})"""
    if len(raw) < 8:
        return None
    def u16(i):
        return (raw[i] & 0xff) << 8 | (raw[i + 1] & 0xff)
    fw, fh, bw, bh = u16(0), u16(2), u16(4), u16(6)
    idx = 8
    blocks = {}
    total = (fw // bw) * (fh // bh)
    for _ in range(total):
        if idx + 6 > len(raw):
            break
        x, y = u16(idx), u16(idx + 2)
        idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", raw[idx:idx + 4])[0]
        idx += 4
        need = bw * bh * 4
        px = raw[idx:idx + need]
        idx += need
        bi = (y // bh) * (fw // bw) + (x // bw)
        blocks[bi] = (x, y, h, px)
    return fw, fh, bw, bh, blocks


class Shell:
    """带标记夹取输出的 shell，避免 telnet 回显干扰（上一版就栽在这）"""

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
        """
        用「先单独打开始标记、再单独打命令、最后单独打结束标记」三段式。
        这样回显里虽然含标记字样，但不会出现"独占一行的标记"，按行就能切干净。
        """
        token = "NXM%d" % (int(time.time() * 1000) % 1000000)
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
        txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", self.buf)
        lines = txt.replace("\r", "").split("\n")
        bi = next((i for i, l in enumerate(lines) if l.strip() == begin), -1)
        ei = next((i for i, l in enumerate(lines) if l.strip() == end), -1)
        body = lines[bi + 1:ei] if (bi >= 0 and ei > bi) else lines
        return "\n".join(l.rstrip() for l in body if l.strip()).strip()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def fingerprint(blocks):
    """把所有块的像素拼起来算 md5，作为"屏幕内容"的指纹"""
    h = hashlib.md5()
    for bi in sorted(blocks):
        h.update(blocks[bi][3])
    return h.hexdigest()[:12], len(blocks)


def main():
    print("########## 1) OSD 时序与数据量 ##########")
    print("  块布局: %dx%d 帧, %dx%d 块 -> %d 列 x %d 行 = %d 块"
          % (FRAME_W, FRAME_H, BLOCK_W, BLOCK_H, COLS, ROWS, NBLOCKS))

    # 全量（哈希全 0）
    st, body, ms = osd_request([0] * NBLOCKS)
    print("\n  [全量] 状态=%s 字节=%d 耗时=%.0f ms" % (st, len(body), ms))
    parsed = parse_osd(body)
    if not parsed:
        print("  ❌ 解析失败")
        return
    fw, fh, bw, bh, blocks = parsed
    print("  解析: 帧 %dx%d 块 %dx%d 实收 %d 块" % (fw, fh, bw, bh, len(blocks)))
    fp_full, n_full = fingerprint(blocks)
    print("  指纹=%s（%d 块）" % (fp_full, n_full))

    # 用真实哈希做增量：第一次应几乎全变，第二次应几乎全不变
    hashes = [0] * NBLOCKS
    for bi, (x, y, h, px) in blocks.items():
        hashes[bi] = h
    st, body2, ms2 = osd_request(hashes)
    p2 = parse_osd(body2)
    n2 = len(p2[4]) if p2 else 0
    print("\n  [增量·同画面] 字节=%d 耗时=%.0f ms 变化块=%d" % (len(body2), ms2, n2))

    # 连续全量 5 次，看帧率上限
    print("\n  [全量 x5] 连续测量：")
    times = []
    for i in range(5):
        st, b, t = osd_request([0] * NBLOCKS)
        times.append(t)
        print("    第 %d 次: %d 字节  %.0f ms" % (i + 1, len(b), t))
    print("  平均 %.0f ms -> 理论最高 %.1f fps（全量）" % (sum(times) / len(times), 1000.0 / (sum(times) / len(times))))

    # 增量轮询（只改动的块）：模拟实时镜像
    print("\n  [增量轮询 x10]（模拟实时镜像：每轮送回真实哈希）")
    hashes = [0] * NBLOCKS
    inc_times, inc_bytes = [], []
    for i in range(10):
        st, b, t = osd_request(hashes)
        p = parse_osd(b)
        if p:
            for bi, (x, y, h, px) in p[4].items():
                hashes[bi] = h
        inc_times.append(t)
        inc_bytes.append(len(b))
        time.sleep(0.05)
    avg_t = sum(inc_times) / len(inc_times)
    avg_b = sum(inc_bytes) / len(inc_bytes)
    print("  平均耗时 %.0f ms，平均字节 %.0f -> 约 %.1f fps，%.0f KB/s"
          % (avg_t, avg_b, 1000.0 / avg_t, avg_b / 1024.0 / (avg_t / 1000.0)))

    print("\n########## 2) 鼠标注入是否生效（E 组的关键前提）##########")
    sh = Shell(IP)
    sh.login()
    print("  shell 已连接，先确认 daemon 路由表:")
    routes = sh.run("grep -ao '/api/v1/[a-z_/]*' "
                    "/opt/usr/apps/nx-remote-controller-mod/nx-remote-controller-daemon 2>/dev/null | sort -u")
    print("  %s" % (routes if routes else "(读不到 daemon 文件)"))

    def snap():
        st, b, _ = osd_request([0] * NBLOCKS)
        p = parse_osd(b)
        return fingerprint(p[4])[0] if p else "?"

    print("\n  取基准帧指纹…")
    base = snap()
    print("  基准 = %s" % base)

    # 尝试几个典型坐标：屏幕中央、以及 mod 网页里 EV 按钮常在的右下角
    for label, (mx, my) in (("屏幕中央(360,240)", (360, 240)),
                            ("右下角(680,440)", (680, 440)),
                            ("菜单键位(360,440)", (360, 440))):
        print("\n  -- 注入鼠标点击 %s --" % label)
        for ep in ("mousemove=%d-%d" % (mx, my), "mousedown=1", "mouseup=1"):
            st, body = http("http://%s/api/v1/input/inject?%s" % (IP, ep), timeout=8)
            print("     inject?%-18s -> 状态=%s %s" % (ep, st, body[:40]))
            time.sleep(0.25)
        time.sleep(0.8)
        after = snap()
        print("     注入后指纹 = %s  %s" % (after, "✅ 变了（鼠标生效！）" if after != base else "❌ 没变"))

    sh.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
