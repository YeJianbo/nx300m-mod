"""E 组关键性能验证：首帧全量 → 后续增量的真实帧率。

这是整个 OSD 方案的地基：
  - 全量 1,383,276 字节 / 815 ms（1.2 fps）→ 不可用
  - 增量 ~51 KB / 108 ms（9.2 fps）→ 可用
如果"转增量"这一步在真机上不成立，整个 E 组就不成立。

同时验证几个接手方（GPT）声明的行为前提：
  1. OSD 在相机出流时才有数据；空转时返回 HTTP 200 + 空体
  2. 拉一次 liveview 能否"激活"出流
  3. POST 必须带正确的 hashs（108×8 小写十六进制）
  4. 零块响应（画面无变化）是合法的
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


def osd(hashs_hex):
    d = urllib.parse.urlencode({"hashs": hashs_hex}).encode()
    t0 = time.time()
    st, body = http("http://%s/api/v1/osd/get" % IP, timeout=25, data=d, method="POST")
    return st, body, (time.time() - t0) * 1000


def parse(raw):
    """返回 (fw, fh, bw, bh, {块索引: hash})"""
    fw, fh, bw, bh = struct.unpack(">HHHH", raw[:8])
    idx, blocks = 8, {}
    per = bw * bh * 4
    cols = fw // bw
    while idx + 4 <= len(raw):
        x, y = struct.unpack(">HH", raw[idx:idx + 4]); idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", raw[idx:idx + 4])[0]; idx += 4
        idx += per
        blocks[(y // bh) * cols + (x // bw)] = h
    return fw, fh, bw, bh, blocks


def hashs_of(hashes):
    return "".join("%08x" % hashes.get(i, 0) for i in range(NBLOCK))


def main():
    print("=== 0. 相机可达性 ===")
    st, body = http("http://%s/api/v1/camera/status" % IP, timeout=6)
    print("  status: %s  %s" % (st, body[:140].decode("utf-8", "replace") if st == 200 else body[:60]))

    print("\n=== 1. 空转时 OSD 是否为空（验证 GPT 声明的前提 1）===")
    st, body, ms = osd("0" * (NBLOCK * 8))
    print("  全零 hashs -> 状态=%s 长度=%s 耗时=%.0f ms" % (st, len(body), ms))

    print("\n=== 2. 拉 liveview 激活出流（前提 2）===")
    for i in range(3):
        st, lv = http("http://%s/api/v1/liveview/get" % IP, timeout=8)
        print("  liveview/get 第%d次: 状态=%s 长度=%s" % (i + 1, st, len(lv) if st == 200 else lv[:40]))
    time.sleep(0.5)

    print("\n=== 3. 首帧全量 ===")
    hashes = {}
    st, body, ms = osd(hashs_of(hashes))
    if st != 200 or len(body) < 12:
        print("  ❌ 仍为空：状态=%s 长度=%s" % (st, len(body)))
        return
    fw, fh, bw, bh, blocks = parse(body)
    print("  状态=%s 长度=%d 耗时=%.0f ms -> %.2f fps" % (st, len(body), ms, 1000.0 / ms))
    print("  帧 %dx%d 块 %dx%d，本次返回 %d 块" % (fw, fh, bw, bh, len(blocks)))
    # 校验长度公式
    expect = 12 + len(blocks) * (8 + bw * bh * 4)
    print("  长度校验: 12 + %d×%d = %d  %s" % (len(blocks), 8 + bw * bh * 4, expect,
                                        "✅" if expect == len(body) else "❌ 不符"))
    hashes = blocks

    print("\n=== 4. 转为增量：连续 20 轮（这是 E 组的地基）===")
    times, bytes_, changed = [], [], []
    for i in range(20):
        st, body, ms = osd(hashs_of(hashes))
        if st != 200:
            print("  第%2d轮 失败 状态=%s" % (i + 1, st)); continue
        if len(body) == 0:
            bytes_.append(0); times.append(ms); changed.append(0)
            print("  第%2d轮 空体（暂不可用）" % (i + 1))
            time.sleep(0.35)
            continue
        _, _, _, _, newb = parse(body)
        hashes.update(newb)
        times.append(ms); bytes_.append(len(body)); changed.append(len(newb))
        if i < 8 or i % 5 == 0:
            print("  第%2d轮 长度=%7d 耗时=%5.0f ms 变化块=%2d" % (i + 1, len(body), ms, len(newb)))
        time.sleep(0.1)

    if times:
        avg_t = sum(times) / len(times)
        avg_b = sum(bytes_) / len(bytes_)
        print("\n  === 增量统计（%d 轮）===" % len(times))
        print("  平均耗时 %.0f ms  ->  %.2f fps" % (avg_t, 1000.0 / avg_t))
        print("  平均字节 %.0f  (%.0f KB/s)" % (avg_b, avg_b / 1024.0 / (avg_t / 1000.0)))
        print("  耗时范围 %.0f ~ %.0f ms" % (min(times), max(times)))
        print("  变化块范围 %d ~ %d" % (min(changed), max(changed)))
        print("  空体轮数 %d / %d" % (sum(1 for b in bytes_ if b == 0), len(bytes_)))
        print()
        print("  结论：%s" % ("✅ 增量可用（≥5 fps）" if 1000.0 / avg_t >= 5
                          else "⚠️ 增量偏慢（%.2f fps）" % (1000.0 / avg_t)))


if __name__ == "__main__":
    main()
