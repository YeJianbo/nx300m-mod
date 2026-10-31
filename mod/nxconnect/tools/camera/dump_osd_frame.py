import os
"""把 OSD 的一帧存成 PNG，并自动判定像素格式。

这是 E 组的最后一块拼图：拿到 OSD 原始像素后，要确认
  - 每像素是不是 RGBA（4 字节）
  - 字节序是 RGBA 还是 BGRA/ARGB
然后存成图片，用眼睛确认"这确实是相机屏幕"。

判定方法（不靠猜）：
  - 统计各通道的取值范围。相机 UI 多为深色底 + 白色文字，
    若某通道恒为 255，那它多半是 Alpha（说明是 XRGB/BGRX 之类）。
  - 再看取景画面区域的颜色是否合理（应当是照片的颜色，而不是反相的）。
存三张候选（RGBA / BGRA / ARGB）各一张，选颜色正常的那张。
"""
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "osd_frame.png")
OUT_CAND = os.path.join(OUT_DIR, "osd_cand_%s.png")


def http(url, timeout=25, data=None, method=None):
    try:
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def u16(raw, i):
    return (raw[i] & 0xff) << 8 | (raw[i + 1] & 0xff)


def fetch_full_osd():
    data = urllib.parse.urlencode({"hashs": "00000000" * 108}).encode()
    st, body = http("http://%s/api/v1/osd/get" % IP, data=data, method="POST")
    if st != 200 or len(body) < 8:
        raise RuntimeError("OSD 请求失败 st=%s len=%s" % (st, len(body) if body else 0))
    return body


def main():
    raw = fetch_full_osd()
    fw, fh, bw, bh = u16(raw, 0), u16(raw, 2), u16(raw, 4), u16(raw, 6)
    print("帧 %dx%d，块 %dx%d，总字节 %d" % (fw, fh, bw, bh, len(raw)))

    # 组装成 RGBA 画布（按协议里的 x/y 偏移放块）
    canvas = bytearray(fw * fh * 4)
    idx = 8
    nblocks = 0
    total = (fw // bw) * (fh // bh)
    chans = [[], [], [], []]
    for _ in range(total):
        if idx + 6 > len(raw):
            break
        x, y = u16(raw, idx), u16(raw, idx + 2)
        idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        idx += 4                      # 跳过 hash
        need = bw * bh * 4
        px = raw[idx:idx + need]
        idx += need
        nblocks += 1
        for j in range(bh):
            row = ((y + j) * fw + x) * 4
            canvas[row:row + bw * 4] = px[j * bw * 4:(j + 1) * bw * 4]
        # 采样若干像素统计各通道范围
        for k in range(0, need, 4 * 97):
            for c in range(4):
                if k + c < len(px):
                    chans[c].append(px[k + c])

    print("实收 %d 块" % nblocks)
    names = ["ch0", "ch1", "ch2", "ch3"]
    for c in range(4):
        v = chans[c]
        if v:
            print("  %s: min=%3d max=%3d 均值=%5.1f  ==255 的比例=%.2f"
                  % (names[c], min(v), max(v), sum(v) / len(v),
                     sum(1 for t in v if t == 255) / len(v)))

    # 存 4 种解释，肉眼选
    interps = {
        "rgba": ("RGBA", 0, 1, 2, 3),
        "bgra": ("RGBA", 2, 1, 0, 3),
        "argb": ("RGBA", 1, 2, 3, 0),
        "rgb0": ("RGBA", 0, 1, 2, None),
    }
    for tag, (mode, ri, gi, bi, ai) in interps.items():
        out = bytearray(fw * fh * 4)
        for i in range(fw * fh):
            s = i * 4
            out[s + 0] = canvas[s + ri]
            out[s + 1] = canvas[s + gi]
            out[s + 2] = canvas[s + bi]
            out[s + 3] = canvas[s + ai] if ai is not None else 255
        img = Image.frombytes("RGBA", (fw, fh), bytes(out)).convert("RGB")
        img.save(OUT_CAND % tag)
        print("  已存 osd_cand_%s.png" % tag)

    # 默认按 RGBA 存主文件
    Image.frombytes("RGBA", (fw, fh), bytes(canvas)).convert("RGB").save(OUT)
    print("\n主文件: %s" % OUT)
    print("候选（挑颜色正常的那张）: %s" % (OUT_CAND % "*"))


if __name__ == "__main__":
    main()
