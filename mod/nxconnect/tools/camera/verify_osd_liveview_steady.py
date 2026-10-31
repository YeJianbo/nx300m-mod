"""稳态测量：liveview + OSD 同时跑，OSD 轮询频率对 liveview 帧率的影响。

目的：回答"OSD 该多久轮询一次"，从而决定要不要换方案。

设计：
  - 对每个 OSD 轮询间隔（不跑 OSD / 500ms / 1000ms / 2000ms / 3000ms）持续跑 30 秒
  - liveview 不限速（每次请求完成立即发下一帧），测**相机能给的真实上限**
  - 记录：liveview 帧率、每帧耗时、OSD 请求次数与平均耗时、OSD 变化块数
  - 关键对比：liveview 单独跑 vs 叠加不同频率 OSD 后掉到多少

为什么 liveview 不限速：App 里设了 500ms 下限，那会掩盖真实上限。
这里要测的是**相机侧的容量**，所以放开跑。

注意：每轮之间要留恢复时间，避免上一轮的重绘影响下一轮。
"""
import http.client
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
NBLOCK = 108
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0


def http_get(path, conn=None, timeout=12):
    """用可复用的连接做 GET；conn 为 None 则新建并返回"""
    own = conn is None
    if own:
        conn = http.client.HTTPConnection(IP, 80, timeout=timeout)
    t0 = time.time()
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        return len(body), (time.time() - t0) * 1000, conn
    except Exception:
        if own:
            try:
                conn.close()
            except Exception:
                pass
        return -1, (time.time() - t0) * 1000, None


def osd_post(hashs_hex, conn=None, timeout=30):
    own = conn is None
    if own:
        conn = http.client.HTTPConnection(IP, 80, timeout=timeout)
    body = urllib.parse.urlencode({"hashs": hashs_hex}).encode()
    t0 = time.time()
    try:
        conn.request("POST", "/api/v1/osd/get", body=body,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        data = r.read()
        return data, (time.time() - t0) * 1000, conn
    except Exception:
        if own:
            try:
                conn.close()
            except Exception:
                pass
        return b"", (time.time() - t0) * 1000, None


def parse_blocks(raw):
    """返回 (块数, {索引: hash})"""
    import struct
    if len(raw) < 12:
        return 0, {}
    fw, fh, bw, bh = struct.unpack(">HHHH", raw[:8])
    idx, per, cols, n = 8, bw * bh * 4, fw // bw, 0
    hashes = {}
    while idx + 4 <= len(raw):
        x, y = struct.unpack(">HH", raw[idx:idx + 4]); idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", raw[idx:idx + 4])[0]; idx += 4
        idx += per
        hashes[(y // bh) * cols + (x // bw)] = h
        n += 1
    return n, hashes


def hashs_of(h):
    return "".join("%08x" % h.get(i, 0) for i in range(NBLOCK))


def ensure_stream():
    for i in range(10):
        n, ms, c = http_get("/api/v1/liveview/get")
        if c:
            c.close()
        if n > 0:
            return True
        time.sleep(0.4)
    return False


def run_case(osd_interval_ms, duration):
    """跑一轮，返回统计"""
    stop = threading.Event()
    lv = {"n": 0, "times": [], "empty": 0}
    od = {"n": 0, "times": [], "changed": 0, "bytes": 0}
    hashes = {}

    def lv_loop():
        conn = http.client.HTTPConnection(IP, 80, timeout=12)
        while not stop.is_set():
            n, ms, conn = http_get("/api/v1/liveview/get", conn)
            if conn is None:
                conn = http.client.HTTPConnection(IP, 80, timeout=12)
                continue
            if n > 0:
                lv["n"] += 1
                lv["times"].append(ms)
            elif n == 0:
                lv["empty"] += 1
        try:
            conn.close()
        except Exception:
            pass

    def osd_loop():
        conn = http.client.HTTPConnection(IP, 80, timeout=30)
        while not stop.is_set():
            t0 = time.time()
            data, ms, conn = osd_post(hashs_of(hashes), conn)
            if conn is None:
                conn = http.client.HTTPConnection(IP, 80, timeout=30)
                continue
            if len(data) >= 12:
                n, nh = parse_blocks(data)
                if n > 0:
                    od["changed"] += n
                    hashes.update(nh)
                od["n"] += 1
                od["times"].append(ms)
                od["bytes"] += len(data)
            wait = osd_interval_ms / 1000.0 - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
        try:
            conn.close()
        except Exception:
            pass

    threads = [threading.Thread(target=lv_loop, daemon=True)]
    if osd_interval_ms > 0:
        threads.append(threading.Thread(target=osd_loop, daemon=True))
    for t in threads:
        t.start()
    time.sleep(duration)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    res = {
        "lv_n": lv["n"],
        "lv_fps": lv["n"] / duration,
        "lv_med": statistics.median(lv["times"]) if lv["times"] else 0,
        "lv_empty": lv["empty"],
        "osd_n": od["n"],
        "osd_hz": od["n"] / duration,
        "osd_med": statistics.median(od["times"]) if od["times"] else 0,
        "osd_changed": od["changed"],
        "osd_bytes": od["bytes"],
    }
    return res


def main():
    print("=== 稳态测试：OSD 轮询频率 vs liveview 帧率 ===")
    print("    每轮 %.0f 秒，liveview 不限速（测相机真实容量）\n" % DURATION)
    if not ensure_stream():
        print("  ❌ 无法出流"); return

    # 每个频率重复 REPEAT 轮，取中位 —— 单轮噪声太大（曾出现"更稀疏反而更差"的非单调结果）
    cases = [0, 1000, 2000, 500]
    REPEAT = 3
    results = []
    for iv in cases:
        label = "不跑 OSD" if iv == 0 else "OSD 每 %d ms" % iv
        fps_list, osd_list = [], []
        print("---------- %s ----------" % label, flush=True)
        for k in range(REPEAT):
            r = run_case(iv, DURATION)
            fps_list.append(r["lv_fps"])
            osd_list.append(r["osd_hz"])
            print("    第 %d 轮: liveview %5.1f fps   OSD %4.2f 次/秒   中位 %4.0f ms/帧"
                  % (k + 1, r["lv_fps"], r["osd_hz"], r["lv_med"]), flush=True)
            time.sleep(3)
        med_fps = statistics.median(fps_list)
        results.append({
            "label": label, "interval": iv,
            "lv_fps": med_fps,
            "lv_min": min(fps_list), "lv_max": max(fps_list),
            "osd_hz": statistics.median(osd_list),
        })
        print("    -> 中位 %.1f fps（范围 %.1f~%.1f）\n" % (med_fps, min(fps_list), max(fps_list)), flush=True)

    print("=== 汇总（各频率 3 轮的中位）===")
    base = results[0]["lv_fps"] if results else 0
    print("  %-16s %10s %12s %10s" % ("OSD 轮询", "liveview中位", "范围", "掉幅"))
    for r in results:
        if r["interval"] == 0:
            drop = "—"
        else:
            drop = "%d%%" % round(100 * (1 - r["lv_fps"] / base)) if base else "?"
        print("  %-16s %7.1f fps %5.1f~%-5.1f %9s"
              % (r["label"], r["lv_fps"], r["lv_min"], r["lv_max"], drop))

    print()
    print("=== 判读 ===")
    print("  若 liveview 掉幅在 OSD 拉到 2000ms 后 <20%%，说明**只需调频**即可，不必换方案。")
    print("  若即使 3000ms 仍掉幅很大，说明是相机 HTTP 串行处理的结构性瓶颈，需要改架构。")


if __name__ == "__main__":
    main()
