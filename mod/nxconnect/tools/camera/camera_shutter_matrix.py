#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_shutter_matrix.py —— 快门时序矩阵探测（本批最重要的脚本）

这是 App 里快门时序的**唯一来源**。当初为了搞清楚"到底要怎么发按键才能真正拍照"，
把三种候选序列逐一在真机上跑，用目录列表判断是否真的产生了照片。

三种候选序列
------------
A: keydown(S1) -> 450ms -> key(S2) -> 1000ms -> keyup(S1)
   即"半按保持住，再点全按，最后松半按"
B: key(S1) -> keyup(S1) -> key(S2)
   即"先松 S1 再发 S2"（半按不保持）
C: 只发 key(S2)
   即"直接全按，不半按"

实测结论（务必遵守）
--------------------
* **只有 A 能拍**。A 是唯一能真正触发快门的序列，App 里的快门就照抄 A。
* B 只对焦：能听到/看到对焦动作，但不释放快门，不产生文件。
* C 无反应：完全不响应。
* 原因：S1/S2 是**机械两段式开关**。全按（S2）需要半按（S1）
  **保持接通**的状态下才成立；一旦松开 S1，S2 的电路就不通了。
  所以 B 那种"先松 S1 再发 S2"在物理上等价于没按快门，C 更是直接无效。

计数陷阱（本脚本的核心设计点，别改成数文件个数！）
--------------------------------------------------
一次快门会写 **2 个文件**：`SAM_xxxx.JPG` + `SAM_xxxx.SRW`。
所以 **绝对不能用"文件数量差值"判断拍了几张** —— 拍 1 张会数成 2 张，
拍 0 张但如果相机产生了别的新文件也会误判。

正确做法：取 basename，**去掉扩展名**得到 `SAM_xxxx` 这个"编号"，
放进 set 里去重，再对新旧集合做差集。
    before = { "SAM_0001", "SAM_0002" }         # 来自 SAM_0001.JPG / SAM_0002.SRW ...
    after  = { "SAM_0001", "SAM_0002", "SAM_0003" }
    new    = after - before  -> {"SAM_0003"}    # 正确：1 张
若改用个数：len(after)-len(before) = 5-4 = 1 ... 但若是首次拍摄，
JPG+SRW 同时出现就会得到 2。所以按编号去重是唯一稳妥的口径。

HTTP 接口（80，nx-remote-controller-daemon / mongoose）
-------------------------------------------------------
    GET /api/v1/input/inject?key=<keysym>
    GET /api/v1/input/inject?keydown=<keysym>
    GET /api/v1/input/inject?keyup=<keysym>
    GET /api/v1/shutter/normal
    GET /api/v1/shutter/silent

8080（busybox httpd + CGI）目录列表：
    GET /cgi-bin/dirlist?p=/DCIM/100PHOTO   -> JSON

注意：注入接口即使失败也返回 HTTP 200，**不能靠状态码判断成败**，
只能靠目录里是否多出新的 SAM 编号。
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

CAMERA_IP = "192.168.5.39"
API_PORT = 80       # nx-remote-controller-daemon (mongoose HTTP)
CGI_PORT = 8080     # busybox httpd + CGI，根 = /opt/usr/nx-ks/nx-rc/capdtm/www

BASE = "http://%s:%d" % (CAMERA_IP, API_PORT)
CGI = "http://%s:%d" % (CAMERA_IP, CGI_PORT)

PHOTO_DIR = "/DCIM/100PHOTO"

# NX500 键码
KEY_S1 = 133        # 快门半按
KEY_S2 = 134        # 快门全按

# 时序参数（序列 A）
A_HOLD_BEFORE_S2_MS = 450     # 半按后等 450ms 再全按（让 AF/AE 稳定）
A_HOLD_AFTER_S2_MS = 1000     # 全按后保持 1000ms 再松 S1
ROUND_GAP_S = 3.0             # 每轮之间的等待（>=3 秒，给相机写卡/回到可拍状态留时间）

# 一次快门写 JPG + SRW 两个文件 —— 去扩展名后才是"一张照片"
RAW_EXTENSIONS = (".jpg", ".jpeg", ".srw", ".srw2", ".png", ".mp4", ".mov")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def http_get(url, timeout=10.0):
    """简单 GET，返回 (status, body_bytes)。永不抛异常，失败返回 (0, b'')。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        except Exception:                          # noqa: BLE001
            return exc.code, b""
    except Exception as exc:                       # noqa: BLE001
        print("      [warn] GET 失败 %s -> %r" % (url, exc))
        return 0, b""


def inject(query):
    """
    调注入接口。query 形如 "keydown=133" / "key=134" / "keyup=133"。
    返回 HTTP 状态码（注意：**即使注入失败也是 200**，别拿它当成败依据）。
    """
    url = "%s/api/v1/input/inject?%s" % (BASE, query)
    status, _ = http_get(url)
    return status


def dirlist(path=PHOTO_DIR):
    """取 8080 CGI 的 JSON 目录列表，返回文件名列表。"""
    url = "%s/cgi-bin/dirlist?p=%s" % (CGI, path)
    status, body = http_get(url)
    if status != 200 or not body:
        print("      [warn] dirlist 失败 status=%s path=%s" % (status, path))
        return []
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        # 兜底：CGI 输出可能不是严格 JSON，用正则粗抓文件名
        return re.findall(r'"([^"]+\.(?:JPG|SRW|jpg|srw))"', text)

    names = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "file", "filename", "f"):
                    if key in item:
                        names.append(str(item[key]))
                        break
    elif isinstance(data, dict):
        for key in ("files", "list", "items", "names"):
            if isinstance(data.get(key), list):
                for item in data[key]:
                    if isinstance(item, str):
                        names.append(item)
                    elif isinstance(item, dict) and "name" in item:
                        names.append(str(item["name"]))
                break
    return names


def shot_ids(names):
    """
    **计数陷阱的解法**：把文件名映射成"照片编号"。
    一次快门写 SAM_xxxx.JPG + SAM_xxxx.SRW 两个文件，
    去扩展名后都是 SAM_xxxx -> set 去重后只算 1 张。
    """
    ids = set()
    for n in names:
        base = n.strip()
        low = base.lower()
        for ext in RAW_EXTENSIONS:
            if low.endswith(ext):
                base = base[: -len(ext)]
                break
        else:
            # 没匹配到已知扩展名也去掉最后一个点后缀，尽量归一
            if "." in base:
                base = base.rsplit(".", 1)[0]
        ids.add(base)
    return ids


def snapshot():
    """抓一次目录快照，返回 (编号集合, 原始文件列表)。"""
    names = dirlist(PHOTO_DIR)
    return shot_ids(names), names


# ---------------------------------------------------------------------------
# 三种候选序列
# ---------------------------------------------------------------------------
def seq_a():
    """A: 半按保持 -> 全按 -> 松半按。**唯一可行。**"""
    print("      step1 keydown(S1=%d)" % KEY_S1)
    print("        -> HTTP %s" % inject("keydown=%d" % KEY_S1))
    time.sleep(A_HOLD_BEFORE_S2_MS / 1000.0)
    print("      step2 key(S2=%d)  [半按保持中]" % KEY_S2)
    print("        -> HTTP %s" % inject("key=%d" % KEY_S2))
    time.sleep(A_HOLD_AFTER_S2_MS / 1000.0)
    print("      step3 keyup(S1=%d)" % KEY_S1)
    print("        -> HTTP %s" % inject("keyup=%d" % KEY_S1))


def seq_b():
    """B: 先松 S1 再发 S2 —— 实测只对焦，不释放快门。"""
    print("      step1 key(S1=%d)" % KEY_S1)
    print("        -> HTTP %s" % inject("key=%d" % KEY_S1))
    time.sleep(0.2)
    print("      step2 keyup(S1=%d)  [半按没有保持]" % KEY_S1)
    print("        -> HTTP %s" % inject("keyup=%d" % KEY_S1))
    time.sleep(0.2)
    print("      step3 key(S2=%d)" % KEY_S2)
    print("        -> HTTP %s" % inject("key=%d" % KEY_S2))


def seq_c():
    """C: 只发 S2 —— 实测无反应。"""
    print("      step1 key(S2=%d)  [没有半按]" % KEY_S2)
    print("        -> HTTP %s" % inject("key=%d" % KEY_S2))


SEQUENCES = [
    ("A", "keydown(S1) -> 450ms -> key(S2) -> 1000ms -> keyup(S1)", seq_a),
    ("B", "key(S1) -> keyup(S1) -> key(S2)  (先松 S1 再发 S2)", seq_b),
    ("C", "key(S2)  (只发全按)", seq_c),
]

EXPECTED = {
    "A": "✅ 拍到（唯一可行）",
    "B": "❌ 只对焦，不产生文件",
    "C": "❌ 无反应",
}


def main():
    print("=" * 74)
    print("NX500 快门时序矩阵  相机=%s" % CAMERA_IP)
    print("目录=%s   每轮间隔 %.1fs" % (PHOTO_DIR, ROUND_GAP_S))
    print("=" * 74)

    # 先确认 daemon 活着（80 端口）；顺便确认目录能列出来
    status, body = http_get("%s/api/v1/camera/info" % BASE)
    print("[pre] /api/v1/camera/info -> HTTP %s %s"
          % (status, body.decode("utf-8", "replace")[:120]))

    baseline_ids, baseline_names = snapshot()
    print("[pre] 基线：目录里 %d 个文件 / %d 个照片编号"
          % (len(baseline_names), len(baseline_ids)))

    results = []
    known_ids = set(baseline_ids)

    for tag, desc, fn in SEQUENCES:
        print("-" * 74)
        print("[序列 %s] %s" % (tag, desc))
        before, _ = snapshot()

        try:
            fn()
        except Exception as exc:                   # noqa: BLE001
            print("      [error] 序列执行异常：%r" % (exc,))

        print("      等待 %.1fs 让相机写卡并回到可拍状态 ..." % ROUND_GAP_S)
        time.sleep(ROUND_GAP_S)

        after, after_names = snapshot()
        # **按去扩展名后的编号做差集**，而不是数文件个数
        new_ids = after - before
        known_ids |= after

        shot = bool(new_ids)
        results.append((tag, desc, shot, sorted(new_ids), len(after_names)))
        print("      结果：%s，新增编号 %d 个 %s"
              % ("拍到 ✅" if shot else "没拍到 ❌", len(new_ids), sorted(new_ids)))
        if not shot:
            print("      （注意：文件数变化 %d -> 无法用于判断，本脚本只认编号差集）"
                  % len(after_names))

    print("=" * 74)
    print("矩阵汇总")
    print("-" * 74)
    print("%-4s %-46s %-10s %s" % ("序列", "说明", "是否拍到", "新增编号"))
    print("-" * 74)
    for tag, desc, shot, new_ids, nfiles in results:
        print("%-4s %-46s %-10s %s"
              % (tag, desc[:46], "拍到" if shot else "没拍到",
                 ",".join(new_ids) if new_ids else "-"))
    print("-" * 74)
    for tag, _, _, _, _ in results:
        print("  %s: %s" % (tag, EXPECTED.get(tag, "")))
    print("-" * 74)
    print("结论：**只有 A 能拍；B 只对焦；C 无反应。**")
    print("原因：S1/S2 是机械两段式开关，全按(S2)必须在半按(S1)保持接通的状态下才成立。")
    print("      App 侧快门时序固定采用序列 A：")
    print("      keydown(S1) -> %dms -> key(S2) -> %dms -> keyup(S1)"
          % (A_HOLD_BEFORE_S2_MS, A_HOLD_AFTER_S2_MS))
    print("重要：不能靠文件数量差值判断张数 —— 一次快门写 JPG+SRW 两个文件，")
    print("      必须按去掉扩展名后的 SAM 编号去重计数（见 shot_ids()）。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
