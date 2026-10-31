"""读回相机屏幕，确认按键注入是否真的改变了 UI。

上一轮发现：注入 MENU 后 OSD 出现 106/108 块变化 —— 但"变化"不等于"菜单真的开了"，
可能只是画面重绘。这里把屏幕渲染成 PNG 存下来，用眼睛确认。

同时验证一个关键的性能问题：**相机 UI 大面积重绘时，增量会退化成接近全量**。
这决定了 E 组"9.2 fps"那个结论在真实使用（操作菜单）下是否成立。
"""
import struct
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
NBLOCK = 108
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "osd_now.png")


def http(url, timeout=30, data=None, method=None):
    try:
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def osd(h):
    d = urllib.parse.urlencode({"hashs": h}).encode()
    t0 = time.time()
    st, body = http("http://%s/api/v1/osd/get" % IP, data=d, method="POST")
    return st, body, (time.time() - t0) * 1000


def canvas_from(raw):
    """把响应合成到整帧 RGBA 画布，返回 (画布, 变化块数)"""
    fw, fh, bw, bh = struct.unpack(">HHHH", raw[:8])
    canvas = bytearray(fw * fh * 4)
    idx, per, cols, n = 8, bw * bh * 4, fw // bw, 0
    hashes = {}
    while idx + 4 <= len(raw):
        x, y = struct.unpack(">HH", raw[idx:idx + 4]); idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", raw[idx:idx + 4])[0]; idx += 4
        px = raw[idx:idx + per]; idx += per
        for j in range(bh):
            dst = ((y + j) * fw + x) * 4
            src = j * bw * 4
            canvas[dst:dst + bw * 4] = px[src:src + bw * 4]
        hashes[(y // bh) * cols + (x // bw)] = h
        n += 1
    return fw, fh, bytes(canvas), n, hashes


def hashs_of(hashes):
    return "".join("%08x" % hashes.get(i, 0) for i in range(NBLOCK))


def save(canvas, fw, fh, path):
    img = Image.frombytes("RGBA", (fw, fh), canvas).convert("RGB")
    img.save(path)
    return path


def inject(p):
    return http("http://%s/api/v1/input/inject?%s" % (IP, p), timeout=8)[0]


def main():
    # 激活出流
    for _ in range(6):
        st, lv = http("http://%s/api/v1/liveview/get" % IP, timeout=8)
        if st == 200 and len(lv) > 0:
            break
        time.sleep(0.3)

    print("=== 1. 取当前屏幕（基线）===")
    st, raw, ms = osd("0" * (NBLOCK * 8))
    fw, fh, canvas, n, hashes = canvas_from(raw)
    save(canvas, fw, fh, OUT.replace(".png", "_before.png"))
    print("  %d 字节 / %.0f ms，%d 块 -> %s" % (len(raw), ms, n, OUT.replace(".png", "_before.png")))

    print("\n=== 2. 注入 MENU，循环取到画面稳定，逐帧存档 ===")
    inject("key=Menu")
    frames = []
    for i in range(14):
        st, raw, ms = osd(hashs_of(hashes))
        if st == 200 and len(raw) >= 12:
            fw, fh, canvas, n, newh = canvas_from(raw)
            hashes.update(newh)
            frames.append((i + 1, len(raw), ms, n))
            if n > 0:
                p = OUT.replace(".png", "_f%02d.png" % (i + 1))
                save(canvas, fw, fh, p)
                print("  第%2d轮 %8d 字节 %6.0f ms 变化 %3d 块 -> 存档 %s" % (i + 1, len(raw), ms, n, p))
            elif i < 3:
                print("  第%2d轮 %8d 字节 %6.0f ms 变化 %3d 块" % (i + 1, len(raw), ms, n))
        time.sleep(0.12)

    print("\n=== 3. 最终画面 ===")
    st, raw, ms = osd("0" * (NBLOCK * 8))
    fw, fh, canvas, n, hashes = canvas_from(raw)
    save(canvas, fw, fh, OUT.replace(".png", "_after.png"))
    print("  -> %s" % OUT.replace(".png", "_after.png"))

    print("\n=== 4. 性能结论：变化块数 vs 响应字节/耗时 ===")
    ch = [(n, s, t) for _, s, t, n in frames if n > 0]
    if ch:
        for n, s, t in ch:
            pct = 100.0 * n / 108
            print("  变化 %3d 块 (%4.0f%%) -> %8d 字节, %6.0f ms" % (n, pct, s, t))
        print()
        print("  ⚠️ 关键：相机 UI 一旦大面积重绘，变化块数接近全屏，")
        print("     增量就退化为接近全量。'9.2 fps' 只适用于**画面静止**时。")
    else:
        print("  未观察到变化（注入可能没生效）")


if __name__ == "__main__":
    main()
