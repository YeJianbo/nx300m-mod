#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_single_shot.py —— 最小可复现的单张拍摄脚本

把 camera_shutter_matrix.py 里唯一可行的**序列 A** 抽出来单独用：
    keydown(S1=133) -> 450ms -> key(S2=134) -> 1000ms -> keyup(S1=133)

为什么是 A（实测结论）
----------------------
* A 能拍；B（先松 S1 再发 S2）只对焦；C（只发 S2）无反应。
* 原因：S1/S2 是机械两段式开关，全按 S2 需要半按 S1 **保持接通**。
  序列 A 的三步正是在模拟这个机械动作。

计数陷阱（本脚本同样遵守）
--------------------------
一次快门会写 **2 个文件**：`SAM_xxxx.JPG` + `SAM_xxxx.SRW`。
所以判断"拍到了几张"**不能用文件数量差值**（会把 1 张数成 2 张），
而是把 basename **去掉扩展名**得到 `SAM_xxxx` 编号，用 set 去重后做差集。

用法
----
    python camera_single_shot.py                 # 拍 1 张
    python camera_single_shot.py --count 3       # 连拍 3 张（每张之间冷却 1200ms）
    python camera_single_shot.py --silent        # 先切电子快门再拍
    python camera_single_shot.py --count 5 --silent
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

CAMERA_IP = "192.168.5.39"
API_PORT = 80       # nx-remote-controller-daemon (mongoose HTTP)
CGI_PORT = 8080     # busybox httpd + CGI（根 /opt/usr/nx-ks/nx-rc/capdtm/www）

BASE = "http://%s:%d" % (CAMERA_IP, API_PORT)
CGI = "http://%s:%d" % (CAMERA_IP, CGI_PORT)
PHOTO_DIR = "/DCIM/100PHOTO"

KEY_S1 = 133        # 快门半按
KEY_S2 = 134        # 快门全按

HOLD_BEFORE_S2_MS = 450     # 半按后等 450ms 再全按
HOLD_AFTER_S2_MS = 1000     # 全按后保持 1000ms 再松 S1
COOLDOWN_MS = 1200          # 连拍时每张之间的冷却（给写卡和机械复位留时间）

RAW_EXTENSIONS = (".jpg", ".jpeg", ".srw", ".srw2", ".png", ".mp4", ".mov")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_get(url, timeout=10.0):
    """GET，返回 (status, body)。不抛异常。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        except Exception:                          # noqa: BLE001
            return exc.code, b""
    except Exception as exc:                       # noqa: BLE001
        print("      [warn] GET %s 失败：%r" % (url, exc))
        return 0, b""


def inject(query):
    """
    调注入接口，query 形如 "keydown=133"。
    返回 HTTP 状态码 —— 注意**注入失败也返回 200**，状态码不能当成败依据。
    """
    return http_get("%s/api/v1/input/inject?%s" % (BASE, query))[0]


def set_silent():
    """切到电子（静音）快门。"""
    return http_get("%s/api/v1/shutter/silent" % BASE)[0]


def set_normal():
    """切回机械快门。"""
    return http_get("%s/api/v1/shutter/normal" % BASE)[0]


# ---------------------------------------------------------------------------
# 目录列表 / 编号去重
# ---------------------------------------------------------------------------
def dirlist(path=PHOTO_DIR):
    """取 8080 CGI 的目录列表，返回文件名列表。"""
    status, body = http_get("%s/cgi-bin/dirlist?p=%s" % (CGI, path))
    if status != 200 or not body:
        print("      [warn] dirlist 失败 status=%s" % status)
        return []
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
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
    把文件名归一成"照片编号"：去掉扩展名。
    SAM_0001.JPG 与 SAM_0001.SRW 都 -> SAM_0001，set 去重后只算 **1 张**。
    这是避免"一次快门两个文件"误判的关键。
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
            if "." in base:
                base = base.rsplit(".", 1)[0]
        ids.add(base)
    return ids


def snapshot(path=PHOTO_DIR):
    """返回 (编号集合, 文件列表)。"""
    names = dirlist(path)
    return shot_ids(names), names


# ---------------------------------------------------------------------------
# 快门：序列 A
# ---------------------------------------------------------------------------
def fire_once(index=None, total=None):
    """
    执行一次序列 A。
    这是 App 里快门时序的唯一形态，不要改成 B 或 C（实测拍不到）。
    """
    tag = "" if index is None else "[%d/%d] " % (index, total)
    print("%s  keydown(S1=%d)  -> HTTP %s" % (tag, KEY_S1, inject("keydown=%d" % KEY_S1)))
    time.sleep(HOLD_BEFORE_S2_MS / 1000.0)
    print("%s  key(S2=%d)      -> HTTP %s   (半按保持中)" % (tag, KEY_S2, inject("key=%d" % KEY_S2)))
    time.sleep(HOLD_AFTER_S2_MS / 1000.0)
    print("%s  keyup(S1=%d)    -> HTTP %s" % (tag, KEY_S1, inject("keyup=%d" % KEY_S1)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="NX500 最小单张拍摄脚本（序列 A：半按保持 -> 全按 -> 松半按）")
    ap.add_argument("--count", type=int, default=1,
                    help="连拍张数，默认 1；每张之间冷却 %dms" % COOLDOWN_MS)
    ap.add_argument("--silent", action="store_true",
                    help="先调 /api/v1/shutter/silent 切电子快门再拍")
    ap.add_argument("--normal", action="store_true",
                    help="先调 /api/v1/shutter/normal 切回机械快门")
    ap.add_argument("--dir", default=PHOTO_DIR,
                    help="目录列表路径，默认 %s" % PHOTO_DIR)
    args = ap.parse_args(argv)

    if args.count < 1:
        print("--count 必须 >= 1")
        return 2

    photo_dir = args.dir

    print("=" * 68)
    print("NX500 单张拍摄   相机=%s  目录=%s  张数=%d" % (CAMERA_IP, photo_dir, args.count))
    print("=" * 68)

    status, body = http_get("%s/api/v1/camera/info" % BASE)
    print("[info] HTTP %s %s" % (status, body.decode("utf-8", "replace")[:120]))

    if args.silent:
        print("[shutter] silent -> HTTP %s" % set_silent())
    elif args.normal:
        print("[shutter] normal -> HTTP %s" % set_normal())

    before_ids, before_names = snapshot(photo_dir)
    print("[before] %d 个文件 / %d 个照片编号 %s"
          % (len(before_names), len(before_ids), sorted(before_ids)[-3:]))

    for i in range(1, args.count + 1):
        print("-" * 68)
        fire_once(i, args.count)
        if i < args.count:
            print("  冷却 %dms ..." % COOLDOWN_MS)
            time.sleep(COOLDOWN_MS / 1000.0)

    time.sleep(COOLDOWN_MS / 1000.0)
    after_ids, after_names = snapshot(photo_dir)

    # **按编号去重后的差集**，不是数文件个数
    new_ids = after_ids - before_ids
    print("=" * 68)
    print("[after ] %d 个文件 / %d 个照片编号" % (len(after_names), len(after_ids)))
    print("新增照片编号 %d 个：%s"
          % (len(new_ids), ", ".join(sorted(new_ids)) if new_ids else "(无)"))
    print("文件级新增 %d 个（含 JPG+SRW 两份，仅供参考，不作为张数依据）"
          % (len(set(after_names) - set(before_names))))
    if len(new_ids) == args.count:
        print("结果：✅ 与请求张数一致")
    elif new_ids:
        print("结果：⚠️ 只拍到 %d 张（请求 %d 张）" % (len(new_ids), args.count))
    else:
        print("结果：❌ 没拍到。检查 daemon 是否活着、相机是否在可拍模式（非回放）、")
        print("      以及 S1/S2 键码是否为 133/134。")
    print("提醒：注入接口即使失败也返回 200，所以只能靠这里的编号差集判断成败。")
    print("=" * 68)
    return 0 if new_ids else 1


if __name__ == "__main__":
    sys.exit(main())
