"""测出相机的**总请求预算**：liveview 与 OSD 合计最多能跑多少请求/秒。

这是所有调度参数的地基。已知：
  - liveview 单独（不限速）：4.5~4.9 fps
  - App 里不开 OSD：6.0 fps（说明 App 的请求可能比裸 HTTP 更省，或有别的差异）
  - App 里开 OSD：2.5 fps

矛盾点：OSD 请求只花 42ms、处理只花 1ms，却让总帧率掉一半。
可能解释：
  A. 总预算有限（约 6/s），两路平分 -> 各 3/s，与实际接近
  B. OSD 请求会**打断** liveview 的流水（服务端单线程，切换上下文有代价）
  C. 响应大小影响（OSD 变化帧 1.3MB 会长时间占用相机）

本脚本直接测：固定 liveview 间隔，扫描 OSD 频率，看**合计完成请求数**是否守恒。
若合计恒定 -> 支持 A（预算受限）。若合计随 OSD 增加而上升 -> 预算未满，另有原因。
"""
import http.client
import statistics
import struct
import sys
import threading
import time
import urllib.parse

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
NBLOCK = 108
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
LV_INTERVAL = float(sys.argv[3]) if len(sys.argv) > 3 else 0.4   # liveview 固定 400ms


def parse_blocks(raw):
    if len(raw) < 12:
        return 0, {}
    fw, fh, bw, bh = struct.unpack(">HHHH", raw[:8])
    idx, per, cols, n = 8, bw * bh * 4, fw // bw, 0
    hs = {}
    while idx + 4 <= len(raw):
        x, y = struct.unpack(">HH", raw[idx:idx + 4]); idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", raw[idx:idx + 4])[0]; idx += 4
        idx += per
        hs[(y // bh) * cols + (x // bw)] = h
        n += 1
    return n, hs


def hashs_of(h):
    return "".join("%08x" % h.get(i, 0) for i in range(NBLOCK))


def run(osd_interval, dur):
    stop = threading.Event()
    lv = {"n": 0, "t": [], "err": 0}
    od = {"n": 0, "t": [], "err": 0, "bytes": 0}
    hashes = {}

    def lv_loop():
        conn = http.client.HTTPConnection(IP, 80, timeout=12)
        while not stop.is_set():
            t0 = time.time()
            try:
                conn.request("GET", "/api/v1/liveview/get")
                r = conn.getresponse(); b = r.read()
                if len(b) > 0:
                    lv["n"] += 1; lv["t"].append((time.time() - t0) * 1000)
            except Exception:
                lv["err"] += 1
                try: conn.close()
                except Exception: pass
                conn = http.client.HTTPConnection(IP, 80, timeout=12)
            w = LV_INTERVAL - (time.time() - t0)
            if w > 0: time.sleep(w)
        try: conn.close()
        except Exception: pass

    def osd_loop():
        if osd_interval <= 0:
            return
        conn = http.client.HTTPConnection(IP, 80, timeout=30)
        while not stop.is_set():
            t0 = time.time()
            try:
                body = urllib.parse.urlencode({"hashs": hashs_of(hashes)}).encode()
                conn.request("POST", "/api/v1/osd/get", body=body,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
                r = conn.getresponse(); data = r.read()
                if len(data) >= 12:
                    n, nh = parse_blocks(data)
                    if n > 0: hashes.update(nh)
                    od["n"] += 1; od["t"].append((time.time() - t0) * 1000)
                    od["bytes"] += len(data)
            except Exception:
                od["err"] += 1
                try: conn.close()
                except Exception: pass
                conn = http.client.HTTPConnection(IP, 80, timeout=30)
            w = osd_interval - (time.time() - t0)
            if w > 0: time.sleep(w)
        try: conn.close()
        except Exception: pass

    ts = [threading.Thread(target=lv_loop, daemon=True)]
    if osd_interval > 0:
        ts.append(threading.Thread(target=osd_loop, daemon=True))
    for t in ts: t.start()
    time.sleep(dur)
    stop.set()
    for t in ts: t.join(timeout=5)
    return {
        "lv_n": lv["n"], "lv_fps": lv["n"] / dur,
        "lv_med": statistics.median(lv["t"]) if lv["t"] else 0,
        "od_n": od["n"], "od_hz": od["n"] / dur,
        "od_med": statistics.median(od["t"]) if od["t"] else 0,
        "od_bytes": od["bytes"], "od_err": od["err"], "lv_err": lv["err"],
    }


def main():
    print("=== 请求预算测试：liveview 固定 %.0f ms 间隔，扫描 OSD 频率 ===" % (LV_INTERVAL * 1000))
    print("    每档 %.0f 秒。合计请求/秒是否守恒，是判断'预算受限'的关键。\n" % DUR)

    # 先出流
    for _ in range(10):
        c = http.client.HTTPConnection(IP, 80, timeout=10)
        try:
            c.request("GET", "/api/v1/liveview/get")
            b = c.getresponse().read()
        except Exception:
            b = b""
        c.close()
        if len(b) > 0:
            break
        time.sleep(0.4)

    cases = [0.0, 2.0, 1.0, 0.5, 0.25]
    rows = []
    for iv in cases:
        r = run(iv, DUR)
        total = r["lv_fps"] + r["od_hz"]
        label = "不跑 OSD" if iv <= 0 else "OSD %.0f ms" % (iv * 1000)
        print("%-14s liveview %5.2f fps (中位%4.0fms)   OSD %5.2f/s (中位%4.0fms)   合计 %5.2f 请求/秒  OSD字节 %d"
              % (label, r["lv_fps"], r["lv_med"], r["od_hz"], r["od_med"], total, r["od_bytes"]), flush=True)
        r["total"] = total; r["label"] = label; r["iv"] = iv
        rows.append(r)
        time.sleep(3)

    print("\n=== 汇总 ===")
    print("  %-14s %12s %12s %16s" % ("OSD 间隔", "liveview", "OSD", "合计请求/秒"))
    for r in rows:
        print("  %-14s %9.2f fps %9.2f/s %13.2f" % (r["label"], r["lv_fps"], r["od_hz"], r["total"]))

    print("\n=== 判读 ===")
    if rows:
        base = rows[0]["total"]
        mx = max(r["total"] for r in rows)
        print("  不跑 OSD 时合计 %.2f 请求/秒；各档最大合计 %.2f 请求/秒" % (base, mx))
        if mx <= base * 1.15:
            print("  -> 合计基本**守恒**：相机总请求预算受限（约 %.1f/秒）。" % base)
            print("     两路必须**分配**这个预算，无法同时拉满。")
        else:
            print("  -> 合计**不守恒**（最大比基线高 %.0f%%）：预算未满，" % (100 * (mx / base - 1)))
            print("     说明掉帧另有原因（而非单纯的请求数竞争）。")


if __name__ == "__main__":
    main()
