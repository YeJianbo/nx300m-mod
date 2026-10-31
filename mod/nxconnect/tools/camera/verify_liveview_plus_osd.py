import os
import os
"""验证分层方案的前提：liveview 与 OSD 能否同时持续输出。

这是 "OSD 叠加在 liveview 之上" 这个架构方案的**地基**。
如果不能同时跑，分层方案就不成立，得改设计。

要回答三个问题：
  1. 一边持续拉 liveview，一边持续发 OSD 请求 —— 两者是否都正常返回？
  2. 同时跑时，OSD 的取景区域是否变成**有内容的实景**（而不是黑）？
     —— 若是，说明 OSD 的黑色区域确实可以被 liveview 填上，分层方案成立且观感正确
  3. 两边同时跑的吞吐各是多少？会不会互相拖慢？

顺带验证第四个问题：
  4. 相机出流时（对着有内容的景物）OSD 的取景区是否仍为黑？
     —— 决定 OSD 黑像素能否安全键控为透明
"""
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

from PIL import Image

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
NBLOCK = 108


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


def hashs_of(h):
    return "".join("%08x" % h.get(i, 0) for i in range(NBLOCK))


def dark_ratio(canvas, fw, fh, x0=0, y0=0, x1=None, y1=None):
    """统计指定矩形的近黑像素占比"""
    x1 = x1 if x1 is not None else fw
    y1 = y1 if y1 is not None else fh
    dark = tot = 0
    for y in range(y0, y1, 2):
        base = y * fw
        for x in range(x0, x1, 2):
            o = (base + x) * 4
            if canvas[o] < 16 and canvas[o + 1] < 16 and canvas[o + 2] < 16:
                dark += 1
            tot += 1
    return 100.0 * dark / max(1, tot)


def main():
    # ---------- 先出流 ----------
    print("=== 0. 激活出流 ===")
    lv_len = 0
    for i in range(8):
        st, lv = http("http://%s/api/v1/liveview/get_low" % IP, timeout=8)
        if st == 200 and len(lv) > 0:
            lv_len = len(lv)
            print("  liveview 出流（第 %d 次），低画质 %d 字节" % (i + 1, lv_len))
            break
        time.sleep(0.3)

    # ---------- 静止时的 OSD 取景区 ----------
    print("\n=== 1. 静止时 OSD 的取景区是否为黑 ===")
    st, raw, ms = osd("0" * (NBLOCK * 8))
    fw, fh, canvas, n, hashes = canvas_from(raw)
    # 取景区大致在中间（避开右侧图标列 x>640 与底部参数栏 y>420）
    d_mid = dark_ratio(canvas, fw, fh, 60, 100, 620, 380)
    print("  中间区域(60,100)-(620,380) 近黑占比: %.1f%%" % d_mid)
    Image.frombytes("RGBA", (fw, fh), canvas).convert("RGB").save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dual_osd_alone.png"))

    # ---------- 同时跑 ----------
    print("\n=== 2. liveview 与 OSD 同时跑 3 秒 ===")
    stop = threading.Event()
    lv_stats = {"n": 0, "bytes": 0, "empty": 0, "err": 0, "times": []}
    osd_stats = {"n": 0, "bytes": 0, "empty": 0, "err": 0, "times": [], "changed": 0}
    hashes_shared = dict(hashes)

    def lv_loop():
        while not stop.is_set():
            t0 = time.time()
            st, body = http("http://%s/api/v1/liveview/get_low" % IP, timeout=8)
            lv_stats["times"].append((time.time() - t0) * 1000)
            if st != 200:
                lv_stats["err"] += 1
            elif len(body) == 0:
                lv_stats["empty"] += 1
            else:
                lv_stats["n"] += 1
                lv_stats["bytes"] += len(body)

    def osd_loop():
        while not stop.is_set():
            st, body, ms = osd(hashs_of(hashes_shared))
            osd_stats["times"].append(ms)
            if st != 200:
                osd_stats["err"] += 1
            elif len(body) < 12:
                osd_stats["empty"] += 1
            else:
                osd_stats["n"] += 1
                osd_stats["bytes"] += len(body)
                _, _, _, nb, nh = canvas_from(body)
                if nb > 0:
                    osd_stats["changed"] += nb
                    hashes_shared.update(nh)
            time.sleep(0.08)

    t1 = threading.Thread(target=lv_loop, daemon=True)
    t2 = threading.Thread(target=osd_loop, daemon=True)
    t1.start(); t2.start()
    time.sleep(3.0)
    stop.set()
    t1.join(timeout=3); t2.join(timeout=3)

    def avg(xs):
        return sum(xs) / len(xs) if xs else 0

    print("  --- liveview ---")
    print("    成功 %d 次, 空体 %d, 错误 %d" % (lv_stats["n"], lv_stats["empty"], lv_stats["err"]))
    print("    平均耗时 %.0f ms -> %.1f fps" % (avg(lv_stats["times"]), 1000.0 / max(1, avg(lv_stats["times"]))))
    print("    总字节 %d" % lv_stats["bytes"])
    print("  --- OSD ---")
    print("    成功 %d 次, 空体 %d, 错误 %d" % (osd_stats["n"], osd_stats["empty"], osd_stats["err"]))
    print("    平均耗时 %.0f ms" % avg(osd_stats["times"]))
    print("    总字节 %d, 变化块累计 %d" % (osd_stats["bytes"], osd_stats["changed"]))

    both_ok = lv_stats["n"] > 0 and osd_stats["n"] > 0
    print("\n  结论：%s" % ("✅ 两者可同时跑，分层方案地基成立" if both_ok
                          else "❌ 同时跑时有一方失败，分层方案需重新设计"))

    # ---------- 同时跑时取一帧 OSD，看取景区 ----------
    print("\n=== 3. 同时跑时 OSD 的取景区 ===")
    st, raw, ms = osd(hashs_of(hashes_shared))
    if st == 200 and len(raw) >= 12:
        fw, fh, canvas, n, hashes_shared = canvas_from(raw)
        d = dark_ratio(canvas, fw, fh, 60, 100, 620, 380)
        print("  中间区域近黑占比: %.1f%%  （若仍接近 100%%，说明 OSD 恒为黑，可安全键控）" % d)
        Image.frombytes("RGBA", (fw, fh), canvas).convert("RGB").save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dual_osd_with_lv.png"))
        print("  已存 _host/dual_osd_with_lv.png")

    print("\n=== 4. 对照：liveview 自身的中间区域亮度 ===")
    st, lv = http("http://%s/api/v1/liveview/get_low" % IP, timeout=8)
    if st == 200 and len(lv) == 259200:
        # 低画质：Y 平面 360x240
        y = lv[:360 * 240]
        vals = [y[i] for i in range(0, len(y), 373)]
        bright = sum(1 for v in vals if v > 100) / len(vals)
        print("  liveview 低画质 Y 平均亮度 %.1f，亮像素占比 %.1f%%" % (sum(vals) / len(vals), bright * 100))
        print("  -> 与 OSD 中间区域的对比见上")


if __name__ == "__main__":
    main()
