"""定位取景帧率的瓶颈：是相机出图慢、WiFi 带宽，还是 App 处理慢。

方法：把"客户端侧"从等式里去掉，只压测相机+网络。
  - 单次请求耗时（冷启动，含 TCP 建连）
  - 持续请求耗时（持久连接，就是 App 实际用的方式）
  - 比较高清(518400)与低清(259200)：如果耗时差≈字节数差/带宽，说明是**带宽受限**
  - 比较"请求完成即发下一帧"的持续吞吐与单帧延迟：
    若持续吞吐远低于 1/单帧延迟，说明相机**无法并行出图**（单核瓶颈）

关键判据：
  - 高清 518400 字节 vs 低清 259200 字节，字节数差 259200
  - 若耗时差 ≈ 259200/带宽，则带宽是瓶颈
  - 若两者耗时**接近**，说明瓶颈不是带宽而是相机出图速度
"""
import http.client
import statistics
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"


def measure(path, n=15, keep_alive=True):
    """持续请求 n 次，返回 (有效耗时列表, 长度集合)"""
    times, sizes = [], set()
    conn = http.client.HTTPConnection(IP, 80, timeout=12)
    try:
        for _ in range(n):
            t0 = time.time()
            conn.request("GET", path)
            r = conn.getresponse()
            body = r.read()
            dt = (time.time() - t0) * 1000
            sizes.add(len(body))
            if len(body) > 0:
                times.append(dt)
            if not keep_alive:
                conn.close()
                conn = http.client.HTTPConnection(IP, 80, timeout=12)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return times, sizes


def stats(label, times):
    if not times:
        print("  %-34s 无有效样本" % label)
        return None
    times = sorted(times)
    med = statistics.median(times)
    print("  %-34s n=%2d  中位 %6.0f ms  最快 %6.0f  最慢 %6.0f  -> %5.2f fps"
          % (label, len(times), med, times[0], times[-1], 1000.0 / med))
    return med


def main():
    print("=== 0. 可达性 ===")
    import socket
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect((IP, 80)); print("  80 端口可达 ✅")
    except Exception as e:
        print("  ❌ 不可达：%s" % e); return
    finally:
        s.close()

    print("\n=== 1. 先出流（前几次可能空体）===")
    for i in range(8):
        times, sizes = measure("/api/v1/liveview/get", n=1)
        if times:
            print("  出流于第 %d 次尝试" % (i + 1)); break
        time.sleep(0.4)

    print("\n=== 2. 持续请求：App 实际的方式（keep-alive）===")
    hi_times, hi_sizes = measure("/api/v1/liveview/get", n=15)
    print("  高清长度集合: %s" % hi_sizes)
    hi = stats("高清 518400 字节", hi_times)

    lo_times, lo_sizes = measure("/api/v1/liveview/get_low", n=15)
    print("  低清长度集合: %s" % lo_sizes)
    lo = stats("低清 259200 字节", lo_times)

    print("\n=== 3. 每次新建连接（对比 keep-alive 的收益）===")
    nc_times, _ = measure("/api/v1/liveview/get", n=8, keep_alive=False)
    nc = stats("高清 每次新连接", nc_times)

    if hi and lo:
        print("\n=== 4. 带宽分析（核心判据）===")
        print("  高清 %d 字节 / %.0f ms  ->  %.2f MB/s (%.1f Mbit/s)"
              % (518400, hi, 518400 / 1024.0 / 1024.0 / (hi / 1000.0),
                 518400 * 8 / 1e6 / (hi / 1000.0)))
        print("  低清 %d 字节 / %.0f ms  ->  %.2f MB/s (%.1f Mbit/s)"
              % (259200, lo, 259200 / 1024.0 / 1024.0 / (lo / 1000.0),
                 259200 * 8 / 1e6 / (lo / 1000.0)))
        print("  字节数比 2.00 倍，耗时比 %.2f 倍" % (hi / lo))
        print()
        if hi / lo > 1.7:
            print("  -> 耗时基本随字节数成比例 => **带宽受限**")
            bw = (518400 - 259200) / 1024.0 / 1024.0 / ((hi - lo) / 1000.0)
            print("     推算有效带宽约 %.2f MB/s (%.1f Mbit/s)" % (bw, bw * 8))
            print("     1806 字节级：这台相机的 WiFi 是 802.11n 单流，理论 72Mbps，")
            print("     实际 TCP 吞吐 ~5-8MB/s 才是正常。当前值明显偏低。")
        elif hi / lo < 1.3:
            print("  -> 耗时与字节数**不成比例** => 瓶颈不是带宽，而是**相机出图速度**")
            print("     （相机渲染一帧就需要这么久，不管传多少字节）")
        else:
            print("  -> 介于两者之间：带宽与出图各有贡献")

    if hi and nc:
        print("\n  keep-alive 收益: %.0f ms -> %.0f ms（省 %.0f ms/帧）" % (nc, hi, nc - hi))

    print("\n=== 5. 与相机端历史实测对照 ===")
    print("  早期单次实测: /liveview/get 178 ms、/get_low 126 ms")
    if hi:
        print("  本次高清中位 %.0f ms，是早期值的 %.1f 倍" % (hi, hi / 178.0))
    if lo:
        print("  本次低清中位 %.0f ms，是早期值的 %.1f 倍" % (lo, lo / 126.0))
    print("  说明：早期值是**空闲时单帧最优**，本次是持续压测；差异反映持续吞吐能力。")


if __name__ == "__main__":
    main()
