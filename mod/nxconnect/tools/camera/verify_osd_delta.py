"""验证真·增量：注入按键让相机屏幕变化，测量增量响应。

背景：上一轮 20 次增量全部返回 0 变化块 —— 因为相机的 OSD 是**静态 UI 叠加层**，
屏幕不动就没变化。所以必须**主动让屏幕变化**才能测到真正的增量。

这同时也是 E 组最终目的的验证：按键注入能否驱动相机 UI，OSD 能否反映出来。

流程：
  1. 建立基线（全量拿到 108 块哈希）
  2. 记录注入前的动态基线（确认屏幕静止 = 0 变化）
  3. 注入一次 MENU 键
  4. 立即连续轮询增量，测量"有变化时"的字节数与耗时
  5. 报告
"""
import struct
import sys
import time
import urllib.parse
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
NBLOCK = 108


def http(url, timeout=25, data=None, method=None):
    try:
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def osd(h):
    d = urllib.parse.urlencode({"hashs": h}).encode()
    t0 = time.time()
    st, body = http("http://%s/api/v1/osd/get" % IP, timeout=25, data=d, method="POST")
    return st, body, (time.time() - t0) * 1000


def parse(raw):
    fw, fh, bw, bh = struct.unpack(">HHHH", raw[:8])
    idx, blocks, per, cols = 8, {}, bw * bh * 4, fw // bw
    while idx + 4 <= len(raw):
        x, y = struct.unpack(">HH", raw[idx:idx + 4]); idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", raw[idx:idx + 4])[0]; idx += 4
        idx += per
        blocks[(y // bh) * cols + (x // bw)] = (x, y, h)
    return blocks


def hashs_of(hashes):
    return "".join("%08x" % (hashes[i][2] if i in hashes else 0) for i in range(NBLOCK))


def inject(params):
    return http("http://%s/api/v1/input/inject?%s" % (IP, params), timeout=8)[0]


def ensure_stream():
    """拉 liveview 直到出流（实测前几次可能是空体）"""
    for i in range(8):
        st, body = http("http://%s/api/v1/liveview/get" % IP, timeout=8)
        if st == 200 and len(body) > 0:
            print("  liveview 出流于第 %d 次尝试（%d 字节）" % (i + 1, len(body)))
            return True
        time.sleep(0.3)
    print("  ⚠️ liveview 8 次尝试均未出流")
    return False


def main():
    print("=== 1. 激活出流 ===")
    if not ensure_stream():
        return

    print("\n=== 2. 建立基线（全量）===")
    hashes = {}
    st, body, ms = osd(hashs_of(hashes))
    if len(body) < 12:
        print("  ❌ 全量失败 状态=%s 长度=%s" % (st, len(body))); return
    blocks = parse(body)
    hashes = blocks
    print("  全量 %d 字节 / %.0f ms，%d 块" % (len(body), ms, len(blocks)))

    print("\n=== 3. 注入前动态基线（应全为 0 变化）===")
    for i in range(3):
        st, body, ms = osd(hashs_of(hashes))
        nb = parse(body) if len(body) >= 12 else {}
        print("  第%d次: %d 字节, 变化 %d 块, %.0f ms" % (i + 1, len(body), len(nb), ms))
        hashes.update(nb)
        time.sleep(0.15)

    print("\n=== 4. 注入 MENU 键，观察 OSD 是否反映 ===")
    st = inject("key=Menu")
    print("  inject?key=Menu -> 状态=%s" % st)

    print("\n  注入后连续 10 轮增量：")
    times, sizes, changes = [], [], []
    for i in range(10):
        st, body, ms = osd(hashs_of(hashes))
        if st != 200:
            print("    第%2d轮 HTTP=%s" % (i + 1, st)); time.sleep(0.12); continue
        if len(body) < 12:
            print("    第%2d轮 空体" % (i + 1)); time.sleep(0.12); continue
        nb = parse(body)
        hashes.update(nb)
        times.append(ms); sizes.append(len(body)); changes.append(len(nb))
        if len(nb) > 0 or i < 3:
            xs = [v[0] for v in nb.values()]; ys = [v[1] for v in nb.values()]
            rng = "x:%d..%d y:%d..%d" % (min(xs), max(xs), min(ys), max(ys)) if nb else "-"
            print("    第%2d轮 %7d 字节 %5.0f ms 变化 %2d 块  %s"
                  % (i + 1, len(body), ms, len(nb), rng))
        time.sleep(0.12)

    print("\n=== 5. 有变化时的增量统计 ===")
    nonzero = [(t, s, c) for t, s, c in zip(times, sizes, changes) if c > 0]
    if nonzero:
        ts = [x[0] for x in nonzero]; ss = [x[1] for x in nonzero]; cs = [x[2] for x in nonzero]
        print("  有变化的轮数: %d / %d" % (len(nonzero), len(times)))
        print("  平均 %d 字节 / %.0f ms  -> %.1f fps（变化 %d~%d 块）"
              % (sum(ss)/len(ss), sum(ts)/len(ts), 1000.0/(sum(ts)/len(ts)), min(cs), max(cs)))
        print()
        print("  长度公式校验（任取一轮）:")
        n = cs[0]
        print("    12 + %d × 12808 = %d  （该轮实际 %d）" % (n, 12 + n*12808, ss[0]))
        print("    %s" % ("✅ 与协议一致" if 12 + n*12808 == ss[0] else "❌ 不一致"))
        print()
        print("  结论：✅ 按键注入能驱动相机 UI，OSD 能反映变化，增量协议成立")
    else:
        print("  ⚠️ 注入 MENU 后 10 轮内没有观察到变化")
        print("     可能原因：相机当前状态对该键无响应，或屏幕变化已在上一次轮询中收敛")

    print("\n=== 6. 恢复：再注入一次 MENU 关闭菜单 ===")
    print("  inject?key=Menu -> 状态=%s" % inject("key=Menu"))


if __name__ == "__main__":
    main()
