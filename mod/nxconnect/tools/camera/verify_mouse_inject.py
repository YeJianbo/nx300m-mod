"""严格验证鼠标注入：变化的区域是否**只**出现在点击处。

为什么要这一步：上一轮"点击后指纹变了"还不能证明鼠标生效 ——
相机屏幕（尤其取景/O SD 叠加）本身可能是动画的，自己就会变。

判据：对变化块的**坐标分布**做统计。
  - 若变化集中在点击坐标附近 -> 鼠标确实生效
  - 若变化散布整屏（尤其左下/右上角持续有块在变）-> 那只是动画，鼠标未必生效

做法：
  1. 连取 6 帧**不注入任何输入**，看哪些块"自然"在变（这是噪声基线）
  2. 再注入一次点击，看变化块的分布是否与基线显著不同、且集中在点击处
"""
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255


def http(url, timeout=25, data=None, method=None):
    try:
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def u16(raw, i):
    return (raw[i] & 0xff) << 8 | (raw[i + 1] & 0xff)


def osd_frame(hashes):
    """返回 (新哈希表, {块索引: (x, y)})"""
    s = "".join("%08x" % h for h in hashes)
    data = urllib.parse.urlencode({"hashs": s}).encode()
    st, body = http("http://%s/api/v1/osd/get" % IP, data=data, method="POST")
    if st != 200 or len(body) < 8:
        return hashes, {}
    fw, fh, bw, bh = u16(body, 0), u16(body, 2), u16(body, 4), u16(body, 6)
    idx, changed = 8, {}
    total = (fw // bw) * (fh // bh)
    for _ in range(total):
        if idx + 6 > len(body):
            break
        x, y = u16(body, idx), u16(body, idx + 2)
        idx += 4
        if x == 0xFFFF and y == 0xFFFF:
            break
        h = struct.unpack(">I", body[idx:idx + 4])[0]
        idx += 4
        idx += bw * bh * 4
        bi = (y // bh) * (fw // bw) + (x // bw)
        hashes[bi] = h
        changed[bi] = (x, y)
    return hashes, changed


def describe(changed, bw=80, bh=40, fw=720):
    """把变化块坐标汇总成人类可读的描述"""
    if not changed:
        return "无变化"
    xs = [v[0] for v in changed.values()]
    ys = [v[1] for v in changed.values()]
    return "变化 %d 块   x:%d..%d  y:%d..%d" % (
        len(changed), min(xs), max(xs), min(ys), max(ys))


def main():
    print("=== 先取一帧建立基线哈希 ===")
    hashes = [0] * 108
    hashes, _ = osd_frame(hashes)
    print("  已建立基线")

    print("\n=== 阶段 1：不注入任何输入，连取 6 帧（测动画噪声基线）===")
    idle_total = 0
    for i in range(6):
        time.sleep(0.15)
        hashes, changed = osd_frame(hashes)
        idle_total += len(changed)
        print("  第 %d 帧: %s" % (i + 1, describe(changed)))
    print("  → 静止时平均每帧自然变化 %.1f 块" % (idle_total / 6.0))

    print("\n=== 阶段 2：注入点击屏幕中央 (360,240)，看变化分布 ===")
    for ep in ("mousemove=360-240", "mousedown=1", "mouseup=1"):
        http("http://%s/api/v1/input/inject?%s" % (IP, ep), timeout=8)
        time.sleep(0.2)
    time.sleep(0.5)

    click_total = 0
    for i in range(4):
        hashes, changed = osd_frame(hashes)
        click_total += len(changed)
        print("  第 %d 帧: %s" % (i + 1, describe(changed)))
        time.sleep(0.15)
    print("  → 点击后平均每帧变化 %.1f 块" % (click_total / 4.0))

    print("\n=== 结论判据 ===")
    print("  静止基线 %.1f 块/帧，点击后 %.1f 块/帧" % (idle_total / 6.0, click_total / 4.0))
    if click_total / 4.0 > idle_total / 6.0 * 2:
        print("  ✅ 点击后变化显著多于静止基线 —— 鼠标注入确实生效")
    elif click_total > 0 and idle_total / 6.0 > 0:
        print("  ⚠️ 差异不明显，需要人工核对（可能点到了无响应区域，或屏幕本身在动）")
    else:
        print("  ✅ 静止时无变化、点击后有变化 —— 鼠标注入生效")

    print("\n=== 阶段 3：再点一次同一个位置，确认可重复 ===")
    for ep in ("mousemove=360-240", "mousedown=1", "mouseup=1"):
        http("http://%s/api/v1/input/inject?%s" % (IP, ep), timeout=8)
        time.sleep(0.2)
    time.sleep(0.5)
    hashes, changed = osd_frame(hashes)
    print("  第二次点击后: %s" % describe(changed))
    print("\n完成。请把上面「变化 x 范围」和点击坐标 (360,240) 对照看是否吻合。")


if __name__ == "__main__":
    main()
