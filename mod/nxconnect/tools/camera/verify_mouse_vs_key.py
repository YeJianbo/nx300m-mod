"""严格判定鼠标注入是否真的能操作相机屏幕。

上一轮的教训：
  1. 我的结论判据写反了（0 vs 0 也判成"生效"），已修。
  2. 现象是：一开始屏幕在变，之后完全静止；静止后点击**不再产生任何变化**。
     所以"点击是否生效"还没有结论。

本轮设计（避免再自欺）：
  A. 先确认屏幕是否真的静止（连取 8 帧，统计总变化块数）
  B. **对照组**：注入一个**键盘事件**（必然会被相机处理，如方向键），
     看屏幕是否会变。若键盘能引起变化而鼠标不能，则说明"X 层输入可达，
     但鼠标事件没被相机的 UI 采纳"。
  C. 用 OSD 自带的取景画面变化做验证：注入 OK 键（应弹出/关闭菜单），
     比较变化块的分布。
  D. 鼠标多点几次不同位置，每次记录变化块数与范围。

这样能把"鼠标是否生效"和"屏幕是否会响应输入"两件事分开。
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


def desc(changed):
    if not changed:
        return "无变化"
    xs = [v[0] for v in changed.values()]
    ys = [v[1] for v in changed.values()]
    return "%2d 块  x:%3d..%3d  y:%3d..%3d" % (len(changed), min(xs), max(xs), min(ys), max(ys))


def inject(params):
    return http("http://%s/api/v1/input/inject?%s" % (IP, params), timeout=8)[0]


def settle(hashes, rounds=8, gap=0.12):
    """连取若干帧让屏幕稳定，返回 (hashes, 最后几帧的变化统计)"""
    counts = []
    for _ in range(rounds):
        time.sleep(gap)
        hashes, ch = osd_frame(hashes)
        counts.append(len(ch))
    return hashes, counts


def main():
    hashes = [0] * 108
    hashes, _ = osd_frame(hashes)

    print("=== A. 屏幕是否静止（让它先稳定）===")
    hashes, counts = settle(hashes, rounds=8)
    print("  连续 8 帧变化块数: %s" % counts)
    static = all(c == 0 for c in counts[-4:])
    print("  后 4 帧全为 0 -> %s" % ("屏幕已静止" if static else "屏幕仍在动（有动画）"))

    print("\n=== B. 对照组：键盘注入（必然可达的输入路径）===")
    for label, key in (("方向键 UP (KP_Up)", "KP_Up"),
                       ("OK (KP_Enter)", "KP_Enter"),
                       ("MENU (Menu)", "Menu")):
        print("  -- 注入 %s --" % label)
        st = inject("key=%s" % key)
        print("     HTTP=%s" % st)
        hashes, counts = settle(hashes, rounds=6)
        total = sum(counts)
        print("     注入后 6 帧变化合计 %d 块  (%s)" % (total, counts))
        print("     %s" % ("✅ 屏幕响应了键盘输入" if total > 0 else "❌ 屏幕毫无反应"))
        # 若弹出了菜单，再按一次 ESC/相同键收回，避免状态残留
        time.sleep(0.3)

    print("\n=== C. 鼠标注入（和键盘用同一套判定）===")
    for label, (mx, my) in (("中央 (360,240)", (360, 240)),
                            ("左下 (100,400)", (100, 400)),
                            ("右上 (620,80)", (620, 80))):
        print("  -- 鼠标点击 %s --" % label)
        inject("mousemove=%d-%d" % (mx, my))
        time.sleep(0.15)
        inject("mousedown=1")
        time.sleep(0.15)
        inject("mouseup=1")
        hashes, counts = settle(hashes, rounds=6)
        total = sum(counts)
        print("     注入后 6 帧变化合计 %d 块  (%s)" % (total, counts))
        print("     %s" % ("✅ 屏幕响应了鼠标输入" if total > 0 else "❌ 屏幕毫无反应"))

    print("\n=== 结论 ===")
    print("  键盘有反应 + 鼠标无反应  -> X 层输入可达，但相机 UI 不采纳鼠标事件")
    print("                              （OSD 点选功能不可行，但键盘路径完全可用）")
    print("  两者都有反应            -> OSD 可以做成完整的触摸遥控")
    print("  两者都无反应            -> 相机可能不在可交互状态（例如正在写卡）")


if __name__ == "__main__":
    main()
