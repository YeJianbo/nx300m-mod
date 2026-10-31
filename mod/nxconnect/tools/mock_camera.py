#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mock_camera.py —— 在本地模拟一台三星 NX500，给 NXConnect（Android App）与 JVM 单元测试连。

没有真机也能开发：这个脚本用标准库起两个 HTTP 服务，把真机上确认过的接口原样复刻一遍。

真机端口分配
------------
  80    nx-remote-controller-daemon（mongoose）—— /api/v1/...
  8080  busybox httpd + CGI            —— /cgi-bin/dirlist、/cgi-bin/thumb
  23    telnet（root 免密）
  21    FTP（root 免密，根目录 = SD 卡 /opt/storage/sdcard）
本模拟器只实现 80 与 8080 两组（daemon 端口默认 80，CGI 端口 = daemon 端口 + 1）。

已确认的真机响应（本脚本按这个复刻，字段名逐字一致）
---------------------------------------------------
GET /api/v1/camera/info
    {"model":"NX500","fw_ver":"1.12","mac_address":"02:30:0d:00:00:01"}
GET /api/v1/camera/status
    {"battery_charging":false,"battery_percent":-1,"battery_level":3,
     "drive":"UNKNOWN","mode":"P","hevc":"off","cameras":[...]}
    * battery_percent 固定 -1：真机硬件不提供电量百分比
GET /api/v1/liveview/get        -> 518400 字节 NV12（高清，实测 ~178ms）
GET /api/v1/liveview/get_low    -> 259200 字节 NV12（低清，实测 ~126ms）
GET /api/v1/input/inject?key=<keysym> / ?keydown= / ?keyup=
GET /api/v1/shutter/normal      /  /api/v1/shutter/silent
GET /cgi-bin/dirlist?p=<路径>    -> JSON 目录列表
GET /cgi-bin/thumb?f=<文件名>&w=<64..1024>&q=<50..95> -> JPEG

必须复刻的两个“坑”
------------------
1) NV12 布局（与 App 的 Nv12Decoder.kt 逐字对齐，输出都是 720x480）：
     高清：Y 720x480 = 345600 + UV 240 行 x 720 字节 = 172800 -> 518400
     低清：Y 360x240 =  86400 + UV 240 行 x 720 字节 = 172800 -> 259200
   低清 Y 的**宽和高都减半**；UV 偏移 = Y 平面字节数 = yW*yH（低清 86400）；
   低清 UV 行跨度 720 字节 ≠ Y 行跨度 360 字节，两者不能混用。
   不变量是「Y 字节数 + UV 字节数 == body.length」。
   （注意：把低清的半分辨率边长当成平面字节数会算出 129600+64800=194400，
     和实测的 259200 差 64800 —— 这个坑在 verify_nv12.py 的文件头有详细记录。）
2) thumb 失败时真机返回 **HTTP 200 + text/plain** 的 "thumb error: source not found"（30 字节），
   所以 App 只看状态码就会把这段错误文本当成 JPEG 帧。
   `srw`（RAW）不在 thumb 白名单里，必定失败；本脚本照此返回同样的 200 + text/plain。

用法
----
  python mock_camera.py                        # 监听 0.0.0.0:80 / :8080（需要管理员/root）
  python mock_camera.py --port 8081            # 非 root：daemon 8081，CGI 8082
  python mock_camera.py --port 0               # 随机端口（CGI = 随机端口+1），便于脚本化测试
  python mock_camera.py --latency-scale 0      # 去掉模拟延迟，跑得快
  python mock_camera.py --nv12-color red       # 换取景动画竖条颜色，便于肉眼看
  python mock_camera.py --freeze               # 冻结取景（两次取帧内容相同，调试用）
  python mock_camera.py --jpeg-backend pil     # 若本地装了 Pillow，缩略图按 w/q 真的缩放
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import posixpath
import signal
import sys
import threading
import time
import traceback
import urllib.parse
import zlib

# --------------------------------------------------------------------------
# 真机实测延迟（秒），乘以 --latency-scale 使用
# --------------------------------------------------------------------------
LAT_HD = 0.178          # /api/v1/liveview/get
LAT_LOW = 0.126         # /api/v1/liveview/get_low
LAT_DIRLIST = 0.030     # /cgi-bin/dirlist
LAT_THUMB = 0.060       # /cgi-bin/thumb
LAT_SHUTTER = 0.350     # 一次快门的机械耗时（S1 0.45s -> S2 -> S1 松开 1.0s，这里只做象征性等待）

# thumb 失败时的真机原文：HTTP 200 + text/plain，正好 30 字节
THUMB_ERROR = b"thumb error: source not found\n"
assert len(THUMB_ERROR) == 30, len(THUMB_ERROR)
# thumb 白名单：srw/raw 不在里面，所以 RAW 取缩略图必定失败（真机行为）
THUMB_WHITELIST = {".jpg", ".jpeg", ".png", ".bmp"}

S1_KEYSYM = "Super_L"   # 半按对焦
S2_KEYSYM = "Super_R"   # 全按快门

# --------------------------------------------------------------------------
# 内置 JPEG（96x64 baseline，quality=70），生成后 base64 内联，
# 这样运行时不需要 Pillow，也不依赖任何外部文件。
# --------------------------------------------------------------------------
JPEG_B64 = {
    "A": (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEEx"
        "NDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7"
        "Ozs7Ozs7Ozs7Ozs7Ozv/wAARCABAAGADASIAAhEBAxEB/8QAGwAAAwEBAAMAAAAAAAAAAAAAAAUGAwcBAgT/xAAxEAAABQMB"
        "BQcDBQEAAAAAAAAAAQIDEgQGEQUTITFV0hQWF3OjpLMHImEVMkFRcUL/xAAYAQEBAQEBAAAAAAAAAAAAAAAAAQIDBP/EABkR"
        "AQADAQEAAAAAAAAAAAAAAAABESFBMf/aAAwDAQACEQMRAD8A5zAEBtAdCvO8rh0m662hodQ2VO1s4I2LasZQkz3mkz4mY9Dk"
        "5vAEBWeId2c19u10g8Q7s5r7drpDTEnAEBWeId2c19u10g8Q7s5r7drpDTEnAEBWeId2c19u10g8Q7s5r7drpDTEnAEBWeId"
        "2c19u10g8Q7s5r7drpDTEnAEB0izLyuHVrroqGu1Da07u0mjYtpzhCjLeSSPiRDnsAG8BSfUJOb31A/L+NIRQFXdtF+o/Uh2"
        "ins+0vMNTxmMkILOP54i9OI2AIB65oTbCnlPVkWG6hltLpN5JTbiVqS5jP8ASC3fn8DdFrrRtEVDj/aGiytmmp9soiM8J/6L"
        "iRGr/Mf3uXBSbgCAdo0RC6UllUqJ5xp19lo2v3NoNWTM87j+xe7B8OI8ajoqaFp1SKg3V0z5U9Qk24khZkZ4I8nIvtUWd3AU"
        "JYAgN4AgCMIAgN4AgAe/T1OL308/M+NQm4CpsFOL1oD8z41CdgJ1eNoCquqp7B9RXK3Z7Ts7zDsM4lFCDxn+OAnIB/fKc3hX"
        "H5fxpFn04XtawgtJZ0+poyfJqoQ5PaRkhJLw2ZY4ZcUefzgeytbTVJM6+mdeeUZTdaf2RrweU5LBluyZf5HGMb10AQFqCzJ7"
        "XTqNo87Sl2pSXm0OJXhKEOmo1FHG/wDevG/+fwMdQ1RNc06lFObS6l4n6hRrkS1kRlkiwUS+5R438R8cAQCoLYQBAbwBAVGE"
        "AQG8AQAOrDTi8qA/M+NQnoCmsZOLwoT8z41BBAZ6vG0BZ3Ta2s6lcVVV0lHtGXIRVtUFnCEke4zzxIScAQGpiQ27kXDy/wBZ"
        "vqB3IuHl/rN9QUwBAKkw27kXDy/1m+oHci4eX+s31BTAEAqTDbuRcPL/AFm+oHci4eX+s31BTAEAqTDbuRcPL/Wb6gdyLh5f"
        "6zfUFMAQCpMVlrWtrOm3FS1dXR7NluclbVB4yhRFuI88TEZAbwBAIiR//9k="
    ),
    "B": (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEEx"
        "NDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7"
        "Ozs7Ozs7Ozs7Ozs7Ozv/wAARCABAAGADASIAAhEBAxEB/8QAGgABAQEBAQEBAAAAAAAAAAAAAAcGBQIDAf/EAC4QAAEDAwID"
        "BwMFAAAAAAAAAAEAAgMEBRESIQYxQTJRYXGBkaETFbEiIzNS0f/EABkBAAMBAQEAAAAAAAAAAAAAAAADBAUBBv/EACMRAAIB"
        "AwQCAwEAAAAAAAAAAAACAQMEoREUIWIx4RITQVH/2gAMAwEAAhEDEQA/AOkiIvIHpAiIgAiyd64uLHvprYQS0gGo2I8Q0cj5"
        "+fgVmKqtqq1+uqqJJTkkanZDc88DkPRWU7N2jWeCd7hVnSOSpopTBUT0zy+nmkhcRgujcWnHdstHaeMJo5Pp3P8AdY538rQA"
        "WDyA3Hzz58l2pZOsarOpxbhZ4ng2aLyx7ZGNexwc1wy1wOQR3r0oikIiIAIiIALOcYXR1LRsooiNVSDrId+prRjp47j0K0an"
        "3Fkr5OIJmvdkRNa1gxyGkH8kqq0SGq8/gmu3xQ4yIi2TOCIiANXwZdHa32yQjTgyREu3B2y0D3O3itepnZJXw3ujdG7BMzWk"
        "46E4PwSqYsi8SFqax+l9u2q6fwIiKMoCIiACwvGVGYLs2pAOmoYDkkdpuxA9NPut0ude7Sy70DosMbM3eKRw7J/w8vnon29T"
        "66kTPgVVT5rpBNkX0qKealnfBPGY5GHDmnovmtyJ1M0Ii/WMdI9rGNLnOOGtAySe5AHU4aozWXyAYOmE/VcQQMaeXzgeqoq4"
        "vDlk+1UpfO1hqpe0Rvob/XP5x84C7SxbqrFR+PEGjRSVXkIiKYcEVIRaew7Y9kG965JuipCI2HbHsN71ySm52ejusemojw8Y"
        "xKzAeMdM9252WcquCJw/NJVxuaSdpQWlo6bjOfhXlE6nb1KfCvj2La4VvK5IHBwRVueRUVcMbcbGMF5z5HC0NssFBaiXwsMk"
        "hORJLgubtjA226+6raIe3qPGkvj2cWui8wuSboqQiTsO2PY3e9ck3RUhEbDtj2G965P/2Q=="
    ),
    "C": (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEEx"
        "NDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7"
        "Ozs7Ozs7Ozs7Ozs7Ozv/wAARCABAAGADASIAAhEBAxEB/8QAFwABAQEBAAAAAAAAAAAAAAAAAAUGB//EABwQAQABBAMAAAAA"
        "AAAAAAAAAAAWAwZVo2TS4v/EABgBAQADAQAAAAAAAAAAAAAAAAACAwYF/8QAGxEBAAEFAQAAAAAAAAAAAAAAAAECAxNSkRL/"
        "2gAMAwEAAhEDEQA/AOSgJogAAAAAAAAAAvQe48dvp9iD3Hjt9Pstw3NZ4qz2to6gi9B7jx2+n2IPceO30+xhuazwz2to6gi9"
        "B7jx2+n2IPceO30+xhuazwz2to6gi9B7jx2+n2IPceO30+xhuazwz2to6gi9B7jx2+n2IPceO30+xhuazwz2to6gi9B7jx2+"
        "n2IPceO30+xhuazwz2to664M3L+Du8kv4O7y77O+KmkGbl/B3eSX8Hd5DxU0gzcv4O7yS/g7vIeKmkGbl/B3eSX8Hd5DxU0g"
        "zcv4O7yS/g7vIeKmkGbl/B3eSX8Hd5DxU5SAzbTgAAAAAAAAAP/Z"
    ),
}
# 启动时自检：解出来的必须真的是 JPEG（FFD8 开头、FFD9 结尾）
EMBEDDED_JPEG = {}
for _k, _v in JPEG_B64.items():
    _raw = base64.b64decode(_v)
    assert _raw[:2] == b"\xff\xd8" and _raw[-2:] == b"\xff\xd9", "内置 JPEG %s 不是有效 JPEG" % _k
    EMBEDDED_JPEG[_k] = _raw
del _k, _v, _raw

COLOR_NAMES = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128),
    "orange": (255, 136, 0),
}


def parse_color(text):
    """--nv12-color：接受颜色名或 #RRGGBB / RRGGBB。"""
    t = (text or "").strip().lower()
    if not t or t in ("none", "off"):
        return None
    if t in COLOR_NAMES:
        return COLOR_NAMES[t]
    h = t.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise argparse.ArgumentTypeError("颜色要写成 red / #33ccff 这种形式，收到 %r" % text)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError("颜色要写成 red / #33ccff 这种形式，收到 %r" % text)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def rgb_to_yuv601(r, g, b):
    """BT.601 limited range（和 App 里 NV12->RGB 的那套系数互逆，够用于造帧）。"""
    y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16
    u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128
    v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128
    return _clamp(y, 16, 235), _clamp(u, 16, 240), _clamp(v, 16, 240)


# --------------------------------------------------------------------------
# 内存里的假 SD 卡（路径用 POSIX 形式；FTP 根 = /opt/storage/sdcard = /mnt/mmc）
# --------------------------------------------------------------------------
class VirtualFs:
    """简单的内存目录树：真机上 dirlist/thumb 读的就是 SD 卡，这里用 dict 代替。"""

    SD_PREFIXES = ("/opt/storage/sdcard", "/mnt/mmc")   # 真机上这两个前缀指向同一张卡

    def __init__(self, capture_start=5):
        self.dirs = {"/"}
        self.files = {}                                  # path -> {"size": int, "mtime": int}
        self.capture_index = int(capture_start)
        self.capture_start = int(capture_start)
        self.mtime_base = 1735689600                     # 2025-01-01 00:00:00 UTC
        self._seed()

    # ---- 路径处理 ----
    @classmethod
    def norm(cls, path):
        p = (path or "").replace("\\", "/").strip()
        if not p:
            return "/"
        p = posixpath.normpath(p)
        if not p.startswith("/"):
            p = "/" + p
        for pre in cls.SD_PREFIXES:
            if p == pre:
                p = "/"
            elif p.startswith(pre + "/"):
                p = p[len(pre):]
        if len(p) > 1 and p.endswith("/"):
            p = p[:-1]
        return p

    def _add_dir(self, path):
        p = self.norm(path)
        cur = ""
        for part in [x for x in p.split("/") if x]:
            cur += "/" + part
            self.dirs.add(cur)
        self.dirs.add("/")
        return p

    def _add_file(self, path, size, mtime=None):
        p = self.norm(path)
        self._add_dir(posixpath.dirname(p) or "/")
        self.files[p] = {"size": int(size), "mtime": int(mtime if mtime else self.mtime_base + len(self.files) * 37)}
        return p

    def _seed(self):
        # 一台刚拍过几张的相机：JPG + SRW 成对出现（一次快门写 2 个文件）
        photos = (
            ("SAM_0001", 4194304, 31457280),
            ("SAM_0002", 3932160, 30408704),
            ("SAM_0003", 4299161, 32505856),
            ("SAM_0004", 3878912, 29982720),
        )
        for i, (name, jpg, srw) in enumerate(photos):
            folder = "/DCIM/100PHOTO" if i < 3 else "/DCIM/101PHOTO"
            self._add_file("%s/%s.JPG" % (folder, name), jpg)
            self._add_file("%s/%s.SRW" % (folder, name), srw)
        for i, (name, size) in enumerate((("NX500_0001", 128849018), ("NX500_0002", 96542720))):
            self._add_file("/MP_ROOT/100VIDEO/%s.MP4" % name, size)
        self._add_file("/DCIM/100PHOTO/NX500_0001.MP4", 52428800)

    # ---- 查询 ----
    def is_dir(self, path):
        return self.norm(path) in self.dirs

    def list_dir(self, path):
        """返回 (子目录列表, 文件列表)，路径不存在时返回 None。"""
        p = self.norm(path)
        if p not in self.dirs:
            return None
        dirs = [d for d in sorted(self.dirs) if d != p and posixpath.dirname(d) == p]
        files = [f for f in sorted(self.files) if posixpath.dirname(f) == p]
        return dirs, files

    def find(self, name):
        """thumb 的 f 参数：允许绝对路径，也允许只给文件名（在树里找唯一匹配）。"""
        if not name:
            return None
        raw = name.replace("\\", "/")
        if raw.startswith("/"):
            p = self.norm(raw)
            return p if p in self.files else None
        base = posixpath.basename(raw)
        hits = [f for f in sorted(self.files) if posixpath.basename(f) == base]
        if not hits:
            return None
        for pref in ("/DCIM/100PHOTO/", "/DCIM/101PHOTO/"):
            for h in hits:
                if h.startswith(pref):
                    return h
        return hits[0]

    def add_capture(self):
        """
        模拟一次快门：真机一次快门会写 **2 个文件**（SAM_xxxx.JPG + SAM_xxxx.SRW），
        所以判断拍了几张不能用文件数量的差值。
        """
        idx = self.capture_index
        self.capture_index += 1
        paths = []
        for ext, size in ((".JPG", 4000000 + idx * 1009), (".SRW", 31000000 + idx * 4096)):
            paths.append(self._add_file("/DCIM/100PHOTO/SAM_%04d%s" % (idx, ext), size))
        return paths


# --------------------------------------------------------------------------
# 合成 NV12 取景帧
# --------------------------------------------------------------------------
def build_nv12_frame(low, tick, frame_no, bar_rgb, bar_width=0):
    """
    造一帧真实长度的 NV12（布局与 App 的 Nv12Decoder.kt 逐字对齐）：

    高清 low=False：Y 平面 720x480 = 345600 字节；UV 240 行 x 720 字节 = 172800；合计 518400
    低清 low=True ：Y 平面 360x240 =  86400 字节；UV 240 行 x 720 字节 = 172800；合计 259200
                    （低清的 Y **宽和高都减半**；UV 平面字节数 = 2 * Y 平面字节数）

    索引（App 侧同一套）：
      yIdx  = (y/2)*yW + (x/2)                        <- 低清，yW = 360（不是 720！）
      uvIdx = uvOffset + (y/2)*uvRowStride + (x/2)*2  <- uvOffset = yW*yH = 86400，stride = 720
    注意：低清的 Y 行跨度是 360 字节，而 UV 行跨度是 720 字节，两者不同，别混用。

    画面内容：竖直渐变的底色 + 一条随时间左右移动的彩色竖条 + 左上角帧号小方块
    （帧号方块保证“连续两次取帧内容不同”，App 端可以借此确认取景是活的）。
    竖条位置按 **输出坐标 720x480** 算，这样两种布局看起来一致。
    """
    out_w, out_h = 720, 480
    if low:
        y_w, y_h, y_scale = out_w // 2, out_h // 2, 2      # 360x240
    else:
        y_w, y_h, y_scale = out_w, out_h, 1                # 720x480
    y_plane = y_w * y_h                                    # 86400 / 345600
    uv_row_stride = (out_w // 2) * 2                       # 720（两种布局都一样）
    uv_rows = out_h // 2                                   # 240
    buf = bytearray(y_plane + uv_row_stride * uv_rows)
    assert len(buf) == (259200 if low else 518400), len(buf)

    # UV 平面先铺中性 128（不铺的话全是 0，画面会整体发绿）
    buf[y_plane:] = b"\x80" * (uv_row_stride * uv_rows)

    # Y 平面底色：上暗下亮的竖直渐变（渐变按输出行算，两种布局观感一致）
    for sy in range(y_h):
        lvl = 28 + ((sy * y_scale) * 26) // max(1, out_h - 1)
        buf[sy * y_w:(sy + 1) * y_w] = bytes([lvl]) * y_w

    # 移动竖条（tick 变化 -> 位置变化，肉眼就能看出取景在动）
    if bar_rgb is not None:
        bw = bar_width if bar_width > 0 else max(8, out_w // 12)
        pos = int((tick * 220.0) % (out_w + bw)) - bw
        x0, x1 = max(0, pos), min(out_w, pos + bw)
        if x1 > x0:
            b_y, b_u, b_v = rgb_to_yuv601(*bar_rgb)
            # Y 平面：竖条按 y_scale 换算（低清 2:1）
            px0, px1 = x0 // y_scale, min(y_w, -(-x1 // y_scale))
            if px1 > px0:
                col = bytes([b_y]) * (px1 - px0)
                for sy in range(y_h):
                    off = sy * y_w + px0
                    buf[off:off + (px1 - px0)] = col
            # UV 平面：色度列 = 输出列/2（两种布局同一套），行跨度 720 字节
            ux0, ux1 = x0 // 2, min(out_w // 2, -(-x1 // 2))
            if ux1 > ux0:
                uv = bytes([b_u, b_v]) * (ux1 - ux0)
                for uy in range(uv_rows):
                    off = y_plane + uy * uv_row_stride + ux0 * 2
                    buf[off:off + len(uv)] = uv

    # 左上角 12x12（输出像素）帧号方块：帧号每请求 +1，保证相邻两帧一定不相等
    lvl = 16 + (frame_no % 16) * 13
    side = 12 // y_scale
    blk = bytes([lvl]) * side
    for sy in range(side):
        buf[sy * y_w:sy * y_w + side] = blk
    return bytes(buf)


# --------------------------------------------------------------------------
# 相机状态（两个端口共用一份）
# --------------------------------------------------------------------------
class MockState:
    def __init__(self, args):
        self.args = args
        self.fs = VirtualFs(capture_start=args.capture_start)
        self.lock = threading.Lock()
        self.started = time.time()
        self.frame_no = 0
        self.request_count = 0
        self.keys_down = set()
        self.key_events = []
        self.shutter_log = []

    # ---- 计数 / 延迟 ----
    def count_request(self):
        with self.lock:
            self.request_count += 1

    def delay(self, seconds):
        """按 --latency-scale 播放真机实测延迟（0 = 全速，测试用）。"""
        d = seconds * max(0.0, self.args.latency_scale)
        if d > 0:
            time.sleep(d)

    # ---- 取景 ----
    def next_frame(self, low):
        self.delay(LAT_LOW if low else LAT_HD)
        with self.lock:
            self.frame_no += 1
            n = self.frame_no
        tick = 0.0 if self.args.freeze else time.monotonic()
        return build_nv12_frame(low, tick, n, self.bar_rgb_value(), self.args.nv12_bar_width)

    def bar_rgb_value(self):
        return self.args.nv12_color

    # ---- 按键注入 / 快门 ----
    def inject(self, kind, keysym):
        """
        真机语义（脚本化实测）：
          * S1 = Super_L（半按对焦），S2 = Super_R（全按快门）
          * 必须 S1 按住不放，期间点 S2 才会拍照；先松 S1 再发 S2 只会对焦
        """
        with self.lock:
            self.key_events.append({"kind": kind, "keysym": keysym, "t": time.time()})
            if kind == "keydown":
                self.keys_down.add(keysym)
                return {"result": "ok"}
            if kind == "keyup":
                self.keys_down.discard(keysym)
                return {"result": "ok"}
            # kind == "key"：一次点击（按下 + 抬起）
            if keysym == S2_KEYSYM and S1_KEYSYM in self.keys_down:
                self._capture_locked("normal", "keysym:S1+S2")
            elif keysym == S2_KEYSYM:
                # S1 没按住 -> 只对焦，不拍照（这正是那个坑）
                self.shutter_log.append({
                    "kind": "focus", "trigger": "keysym:S2(no S1)", "captured": False,
                    "files": [], "t": time.time(),
                })
            return {"result": "ok"}

    def capture(self, kind, trigger):
        """API 快门：真机 daemon 收到这个请求后自己走 S1->S2->松 S1 的时序。"""
        self.delay(LAT_SHUTTER)
        with self.lock:
            return self._capture_locked(kind, trigger)

    def _capture_locked(self, kind, trigger):
        files = self.fs.add_capture()
        entry = {"kind": kind, "trigger": trigger, "captured": True, "files": files, "t": time.time()}
        self.shutter_log.append(entry)
        if not self.args.quiet:
            print("[mock] 快门(%s) 触发源=%s -> %s" % (kind, trigger, ", ".join(files)), flush=True)
        return entry

    # ---- 其它接口用的 JSON ----
    def cameras_json(self):
        # 真机 cameras 数组内部字段没有逐字确认，这里给出 App 常用的三个字段
        return [{
            "model": self.args.model,
            "ip": self.args.advertise_ip,
            "connected": True,
        }]

    def snapshot(self, ports):
        with self.lock:
            return {
                "uptime": round(time.time() - self.started, 3),
                "ports": ports,
                "requests": self.request_count,
                "frame_no": self.frame_no,
                "keys_down": sorted(self.keys_down),
                "shutter_count": sum(1 for e in self.shutter_log if e["captured"]),
                "shutter_log": self.shutter_log,
                "file_count": len(self.fs.files),
                "dir_count": len(self.fs.dirs),
                "nv12_color": self.args.nv12_color,
                "freeze": bool(self.args.freeze),
                "model": self.args.model,
                "fw_ver": self.args.fw_ver,
                "mac_address": self.args.mac,
                "sd_root": "/opt/storage/sdcard",
            }


# --------------------------------------------------------------------------
# HTTP 处理
# --------------------------------------------------------------------------
class _BaseHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # 保持长连接：App 端 OkHttp 会复用
    server_version = "NX500-mock"
    sys_version = ""

    # BaseHTTPRequestHandler 默认往 stderr 打日志，这里自己控制
    def log_message(self, fmt, *args):
        return

    def log_request(self, code="-", size="-"):
        return

    @property
    def state(self):
        return self.server.state

    @property
    def cfg(self):
        return self.server.state.args

    def _log(self, note=""):
        if self.cfg.quiet:
            return
        try:
            port = self.server.server_address[1]
        except Exception:
            port = 0
        print("[mock:%d] %s %s %s" % (port, self.command, self.path, note), flush=True)

    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # App 切走取景页时会直接断连，属于正常现象
            self.close_connection = True

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=True, separators=(",", ":")), "application/json")

    def _text(self, text, code=200):
        self._send(code, text, "text/plain; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        try:
            self.route(parsed.path, query)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:                       # noqa: BLE001 - 模拟器要尽量不死
            traceback.print_exc()
            try:
                self._json({"result": "error", "message": str(exc)}, 500)
            except Exception:
                pass

    def do_HEAD(self):
        self.do_GET()

    def route(self, path, query):                       # pragma: no cover - 由子类实现
        raise NotImplementedError


def _int_param(query, name, default):
    try:
        return int((query.get(name) or [default])[0])
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# 端口 80：nx-remote-controller-daemon
# --------------------------------------------------------------------------
class DaemonHandler(_BaseHandler):
    def route(self, path, query):
        self.state.count_request()
        self._log()

        if path in ("/", "/index.html"):
            return self._text(
                "NX500 mock daemon\n"
                "  GET /api/v1/camera/info\n"
                "  GET /api/v1/camera/status\n"
                "  GET /api/v1/liveview/get        (518400 字节 NV12)\n"
                "  GET /api/v1/liveview/get_low    (259200 字节 NV12)\n"
                "  GET /api/v1/input/inject?key=|keydown=|keyup=<keysym>\n"
                "  GET /api/v1/shutter/normal|silent\n"
                "  GET /__mock__/state|shutter_log|reset   (仅模拟器有)\n"
            )
        if path == "/api/v1/camera/info":
            return self.camera_info()
        if path == "/api/v1/camera/status":
            return self.camera_status()
        if path == "/api/v1/liveview/get":
            return self.liveview(low=False)
        if path == "/api/v1/liveview/get_low":
            return self.liveview(low=True)
        if path == "/api/v1/input/inject":
            return self.inject(query)
        if path == "/api/v1/shutter/normal":
            return self.shutter("normal")
        if path == "/api/v1/shutter/silent":
            return self.shutter("silent")
        if path.startswith("/__mock__/"):
            return self.mock_endpoint(path)
        return self._json({"result": "error", "message": "not found: %s" % path}, 404)

    # ---- /api/v1/camera/info ----
    def camera_info(self):
        # 字段名与真机逐字一致，别改：model / fw_ver / mac_address
        self._json({"model": self.cfg.model, "fw_ver": self.cfg.fw_ver, "mac_address": self.cfg.mac})

    # ---- /api/v1/camera/status ----
    def camera_status(self):
        self._json({
            "battery_charging": False,
            "battery_percent": -1,          # 真机固定 -1：硬件不给电量百分比
            "battery_level": self.cfg.battery_level,
            "drive": "UNKNOWN",
            "mode": self.cfg.mode,          # 真机默认 P
            "hevc": self.cfg.hevc,          # "off" / "on"
            "cameras": self.state.cameras_json(),
        })

    # ---- /api/v1/liveview/get[_low] ----
    def liveview(self, low):
        body = self.state.next_frame(low)
        # 真机 Content-Type 未逐字确认，按二进制流给出
        self._send(200, body, "application/octet-stream")

    # ---- /api/v1/input/inject ----
    def inject(self, query):
        for kind in ("key", "keydown", "keyup"):
            vals = query.get(kind) or []
            if vals and vals[0]:
                keysym = vals[0]
                self.state.inject(kind, keysym)
                # 真机返回体未逐字确认（只知道 200 + 能生效），这里给最小 JSON
                return self._json({"result": "ok"})
        return self._json({"result": "error", "message": "需要 key= / keydown= / keyup= 之一"}, 400)

    # ---- /api/v1/shutter/normal|silent ----
    def shutter(self, kind):
        self.state.capture(kind, "api:/api/v1/shutter/%s" % kind)
        return self._json({"result": "ok"})

    # ---- 模拟器专用端点（真机没有，别在 App 里依赖） ----
    def mock_endpoint(self, path):
        if path == "/__mock__/state":
            ports = {"daemon": self.server.server_address[1], "cgi": self.cfg.cgi_port}
            return self._json(self.state.snapshot(ports))
        if path == "/__mock__/shutter_log":
            with self.state.lock:
                return self._json(list(self.state.shutter_log))
        if path == "/__mock__/files":
            fs = self.state.fs
            return self._json({
                "dirs": sorted(fs.dirs),
                "files": [{"path": p, "size": v["size"], "mtime": v["mtime"]} for p, v in sorted(fs.files.items())],
            })
        if path == "/__mock__/reset":
            with self.state.lock:
                self.state.shutter_log = []
                self.state.key_events = []
                self.state.keys_down.clear()
            return self._json({"result": "ok"})
        return self._json({"result": "error", "message": "unknown mock endpoint"}, 404)


# --------------------------------------------------------------------------
# 端口 8080：busybox httpd + CGI
# --------------------------------------------------------------------------
class CgiHandler(_BaseHandler):
    def route(self, path, query):
        self.state.count_request()
        self._log()

        if path in ("/", "/index.html"):
            return self._text("NX500 mock CGI: /cgi-bin/dirlist?p=  /cgi-bin/thumb?f=&w=&q=\n")
        if path == "/cgi-bin/dirlist":
            return self.dirlist(query)
        if path == "/cgi-bin/thumb":
            return self.thumb(query)
        # 真机上未知 CGI 路径就是 404 + 文本，App 若把 body 当帧就会拿到这段字
        return self._text("cgi error: unknown endpoint\n", 404)

    # ---- /cgi-bin/dirlist?p=<路径> ----
    def dirlist(self, query):
        self.state.delay(LAT_DIRLIST)
        p = (query.get("p") or ["/"])[0]
        listing = self.state.fs.list_dir(p)
        if listing is None:
            # 和 thumb 一样：出错时真机也是 200 + text/plain，不是 HTTP 错误码
            return self._text("dirlist error: no such directory\n")
        dirs, files = listing
        fs = self.state.fs
        out = []
        if self.cfg.dirlist_parent_entry and fs.norm(p) != "/":
            out.append({"name": "..", "size": 0, "dir": True, "is_dir": True, "mtime": fs.mtime_base})
        for d in dirs:
            out.append({"name": posixpath.basename(d), "size": 0, "dir": True, "is_dir": True, "mtime": fs.mtime_base})
        for f in files:
            info = fs.files[f]
            # dir 与 is_dir 同时给出，兼容不同解析习惯；size 是字节数
            out.append({"name": posixpath.basename(f), "size": info["size"], "dir": False,
                        "is_dir": False, "mtime": info["mtime"]})
        self._json(out)

    # ---- /cgi-bin/thumb?f=<文件名>&w=&q= ----
    def thumb(self, query):
        self.state.delay(LAT_THUMB)
        name = (query.get("f") or [""])[0]
        w = _int_param(query, "w", 320)
        q = _int_param(query, "q", 80)

        if self.cfg.strict_thumb_params and not (64 <= w <= 1024 and 50 <= q <= 95):
            return self._text(THUMB_ERROR)
        w = _clamp(w, 64, 1024)        # 真机范围 w=64..1024、q=50..95，超范围这里按边界夹取
        q = _clamp(q, 50, 95)

        path = self.state.fs.find(name)
        ext = posixpath.splitext(path or name)[1].lower()
        if path is None or ext not in THUMB_WHITELIST:
            # 关键坑：RAW(srw) 不在白名单里，真机返回 HTTP 200 + text/plain 的 30 字节错误文本。
            # App 只看状态码的话，就会把这段文字当成缩略图/帧数据。
            return self._text(THUMB_ERROR)
        return self._send(200, self.thumb_bytes(path, w, q), "image/jpeg")

    def thumb_bytes(self, path, w, q):
        if self.cfg.jpeg_backend == "pil":
            data = _pil_jpeg(path, w, q)
            if data:
                return data
        # 内置 JPEG（不同文件名给不同图案，便于确认请求打到了哪个文件）
        key = ("A", "B", "C")[zlib.crc32(posixpath.basename(path).encode("utf-8")) % 3]
        return EMBEDDED_JPEG[key]


def _pil_jpeg(path, w, q):
    """可选：本地有 Pillow 时按 w/q 真的缩放（仅 --jpeg-backend pil 时使用）。"""
    try:
        import io
        from PIL import Image
    except Exception:
        return None
    try:
        key = ("A", "B", "C")[zlib.crc32(posixpath.basename(path).encode("utf-8")) % 3]
        im = Image.open(io.BytesIO(EMBEDDED_JPEG[key])).convert("RGB")
        scale = max(1, w // im.width + (1 if w % im.width else 0))
        if scale > 1:
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q)
        return buf.getvalue()
    except Exception as exc:                            # noqa: BLE001
        print("[mock] Pillow 生成缩略图失败，回退内置 JPEG: %s" % exc, flush=True)
        return None


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="本地模拟三星 NX500（daemon 端口 80 + CGI 端口 8080）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python mock_camera.py --port 8081 --latency-scale 0 --nv12-color red",
    )
    p.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（真机也是全网卡）")
    p.add_argument("--port", type=int, default=80, help="daemon 端口，真机 80；非 root 环境改成 8081 之类")
    p.add_argument("--cgi-port", type=int, default=0, help="CGI 端口，默认 = daemon 端口 + 1（真机 8080）")
    p.add_argument("--model", default="NX500")
    p.add_argument("--fw-ver", default="1.12", help="真机 /camera/info 里的 fw_ver")
    p.add_argument("--mac", default="02:30:0d:00:00:01", help="真机 mac_address")
    p.add_argument("--advertise-ip", default="192.168.5.39", help="status.cameras 里报的相机 IP")
    p.add_argument("--mode", default="P", help="曝光模式，真机默认 P")
    p.add_argument("--hevc", default="off", choices=["off", "on"])
    p.add_argument("--battery-level", type=int, default=3)
    p.add_argument("--capture-start", type=int, default=5, help="模拟快门写出的起始文件号 SAM_xxxx")
    p.add_argument("--nv12-color", type=parse_color, default=parse_color("#33ccff"),
                   help="取景动画竖条颜色：red/green/blue/... 或 #RRGGBB，off 关闭竖条")
    p.add_argument("--nv12-bar-width", type=int, default=0, help="竖条像素宽度，0 = 自动（约 1/12 宽）")
    p.add_argument("--freeze", action="store_true", help="冻结取景动画（两次取帧内容相同，调试用）")
    p.add_argument("--latency-scale", type=float, default=1.0,
                   help="按真机实测延迟播放：hd 178ms / low 126ms / thumb 60ms / dirlist 30ms；0 = 全速")
    p.add_argument("--jpeg-backend", choices=["embedded", "pil"], default="embedded",
                   help="缩略图来源：embedded = 内置 JPEG（纯标准库）；pil = 用本地 Pillow 按 w/q 缩放")
    p.add_argument("--strict-thumb-params", action="store_true",
                   help="w/q 超出真机范围时返回错误文本（默认为夹取到边界）")
    p.add_argument("--dirlist-parent-entry", action="store_true", help="dirlist 里带一行 '..'")
    p.add_argument("--ready-file", default="", help="启动完成后把 'host daemon_port cgi_port' 写进该文件")
    p.add_argument("--quiet", action="store_true", help="不打请求日志")
    return p.parse_args(argv)


def _print_banner(args, daemon_port, cgi_port):
    host = args.host
    show = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("=" * 70)
    print("NX500 mock camera   daemon=%s:%d   cgi=%s:%d" % (host, daemon_port, host, cgi_port))
    print("  http://%s:%d/api/v1/camera/info" % (show, daemon_port))
    print("  http://%s:%d/api/v1/camera/status" % (show, daemon_port))
    print("  http://%s:%d/api/v1/liveview/get       -> 518400 字节 NV12" % (show, daemon_port))
    print("  http://%s:%d/api/v1/liveview/get_low   -> 259200 字节 NV12（Y 平面 360x240）" % (show, daemon_port))
    print("  http://%s:%d/cgi-bin/dirlist?p=/DCIM/100PHOTO" % (show, cgi_port))
    print("  http://%s:%d/cgi-bin/thumb?f=SAM_0001.JPG&w=320&q=80" % (show, cgi_port))
    print("  http://%s:%d/cgi-bin/thumb?f=SAM_0001.SRW&w=320&q=80  -> 200 + text/plain（RAW 必定失败）"
          % (show, cgi_port))
    print("  快门时序：keydown(Super_L) -> 0.45s -> key(Super_R) -> 1.0s -> keyup(Super_L)")
    print("  取景竖条颜色=%s  延迟系数=%.2f  %s" % (args.nv12_color, args.latency_scale,
                                            "（已冻结动画）" if args.freeze else ""))
    print("=" * 70, flush=True)


def main(argv=None):
    args = parse_args(argv)
    state = MockState(args)

    # daemon 端口先绑（--port 0 时由系统分配，CGI 端口再跟着算）
    try:
        daemon = http.server.ThreadingHTTPServer((args.host, args.port), DaemonHandler)
    except OSError as exc:
        print("[mock] 无法监听 %s:%d -> %s" % (args.host, args.port, exc), file=sys.stderr, flush=True)
        print("[mock] 提示：80 端口被占用或没权限时，改成 --port 8081（CGI 会自动用 8082）", file=sys.stderr, flush=True)
        return 2
    daemon.state = state
    daemon_port = daemon.server_address[1]
    cgi_port = args.cgi_port if args.cgi_port else daemon_port + 1
    args.cgi_port = cgi_port

    try:
        cgi = http.server.ThreadingHTTPServer((args.host, cgi_port), CgiHandler)
    except OSError as exc:
        print("[mock] 无法监听 %s:%d -> %s" % (args.host, cgi_port, exc), file=sys.stderr, flush=True)
        daemon.server_close()
        return 2
    cgi.state = state

    servers = [daemon, cgi]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, name="serve-%d" % srv.server_address[1],
                         daemon=True).start()

    _print_banner(args, daemon_port, cgi_port)
    if args.ready_file:
        try:
            with open(args.ready_file, "w", encoding="utf-8") as fp:
                fp.write("%s %d %d\n" % (args.host, daemon_port, cgi_port))
        except OSError as exc:
            print("[mock] 写 ready-file 失败: %s" % exc, file=sys.stderr, flush=True)

    stop = threading.Event()

    def _on_signal(signum, frame):                      # noqa: ARG001
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)              # Windows 上 terminate() 是硬杀，收不到 SIGTERM
        except (ValueError, OSError):
            pass

    try:
        while not stop.is_set():
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        print("[mock] 已停止", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
