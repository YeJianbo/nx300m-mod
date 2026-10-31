#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_mock_camera.py —— 起一个 mock_camera.py（临时端口），逐项验证它符合真机契约。

验证项（对应真机实测事实）：
  * /api/v1/camera/info   字段名逐字一致：model / fw_ver / mac_address
  * /api/v1/camera/status battery_percent == -1（真机硬件不给电量百分比）
  * /api/v1/liveview/get        -> 518400 字节（720x480 高清 NV12）
  * /api/v1/liveview/get_low    -> 259200 字节（Y 平面 360x240，宽高都减半！）
  * 连续两帧内容不同（取景是活的）
  * /cgi-bin/dirlist 能被解析出文件名与大小
  * /cgi-bin/thumb?f=*.JPG -> 真 JPEG（FF D8 开头）
  * /cgi-bin/thumb?f=*.SRW -> **HTTP 200 + text/plain**（RAW 不在白名单，复现“把错误 body 当帧”的坑）
  * 快门：一次快门写 2 个文件（JPG + SRW）；S1 按住期间点 S2 才拍照

用法：
    python verify_mock_camera.py              # 全部通过退出码 0
    python verify_mock_camera.py --verbose    # 顺便把 mock 的请求日志打出来
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MOCK = os.path.join(HERE, "mock_camera.py")

# 真机实测常量，写死在这里当契约
HD_LEN = 518400
LOW_LEN = 259200
THUMB_ERROR = b"thumb error: source not found\n"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
class Checker:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print("PASS  %-34s %s" % (name, detail))
        else:
            self.failed += 1
            print("FAIL  %-34s %s" % (name, detail))
        return bool(ok)

    def eq(self, name, got, want):
        return self.check(name, got == want, "got=%r want=%r" % (got, want))

    def skip(self, name, reason):
        self.skipped += 1
        print("SKIP  %-34s %s" % (name, reason))


def http_get(url, timeout=10.0):
    """返回 (status, headers_lower, body)；HTTP 错误码也照样返回 body。"""
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Connection": "close"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return exc.code, headers, exc.read()


def get_json(url, timeout=10.0):
    status, headers, body = http_get(url, timeout)
    return status, headers, json.loads(body.decode("utf-8"))


def port_free(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_ports(attempts=40):
    """找一对相邻的可用端口（daemon 用 base，CGI 用 base+1）。"""
    seed = (os.getpid() * 7919 + int(time.time())) % 20000
    for i in range(attempts):
        base = 20000 + (seed + i * 2) % 30000
        if port_free(base) and port_free(base + 1):
            return base
    return 0


# --------------------------------------------------------------------------
# 启动 / 关闭 mock
# --------------------------------------------------------------------------
class MockProcess:
    def __init__(self, python, base_port, host="127.0.0.1", verbose=False):
        self.python = python
        self.base_port = base_port
        self.host = host
        self.verbose = verbose
        self.proc = None
        self.log_path = None
        self.port = base_port
        self.cgi_port = base_port + 1

    def start(self):
        fd, self.log_path = tempfile.mkstemp(prefix="mock_camera_", suffix=".log")
        self.log_fp = os.fdopen(fd, "wb")
        cmd = [
            self.python, MOCK,
            "--host", self.host,
            "--port", str(self.base_port),
            "--latency-scale", "0",     # 测试时不要真机延迟
            "--quiet",
        ]
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, cwd=HERE, stdout=self.log_fp, stderr=subprocess.STDOUT)
        return self

    def url(self, path, cgi=False):
        port = self.cgi_port if cgi else self.port
        return "http://%s:%d%s" % (self.host, port, path)

    def wait_ready(self, timeout=20.0):
        """轮询 /api/v1/camera/info 直到起来；进程提前退出就直接失败。"""
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False, "mock 进程已退出（exit=%s）" % self.proc.returncode
            try:
                status, _, body = http_get(self.url("/api/v1/camera/info"), timeout=2.0)
                if status == 200:
                    json.loads(body.decode("utf-8"))
                    return True, "daemon=%d cgi=%d" % (self.port, self.cgi_port)
                last = "status=%s" % status
            except Exception as exc:                       # noqa: BLE001 - 起来之前连不上是正常的
                last = str(exc)
            time.sleep(0.2)
        return False, "等待超时（最后错误：%s）" % last

    def log_tail(self, lines=30):
        try:
            self.log_fp.flush()
            with open(self.log_path, "rb") as fp:
                data = fp.read().decode("utf-8", "replace").splitlines()
            return data[-lines:]
        except Exception:
            return []

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        try:
            self.log_fp.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 各项断言
# --------------------------------------------------------------------------
def check_info(ck, mp):
    status, headers, body = http_get(mp.url("/api/v1/camera/info"))
    if not ck.eq("info HTTP 200", status, 200):
        return
    data = json.loads(body.decode("utf-8"))
    ck.eq("info 字段名逐字一致", sorted(data.keys()), ["fw_ver", "mac_address", "model"])
    ck.eq("info.model", data.get("model"), "NX500")
    ck.eq("info.fw_ver", data.get("fw_ver"), "1.12")
    ck.eq("info.mac_address", data.get("mac_address"), "02:30:0d:00:00:01")


def check_status(ck, mp):
    status, headers, body = http_get(mp.url("/api/v1/camera/status"))
    if not ck.eq("status HTTP 200", status, 200):
        return
    data = json.loads(body.decode("utf-8"))
    for key in ("battery_charging", "battery_percent", "battery_level", "drive", "mode", "hevc", "cameras"):
        ck.check("status 有字段 %s" % key, key in data, "值=%r" % (data.get(key),))
    ck.eq("status.battery_percent == -1", data.get("battery_percent"), -1)
    ck.eq("status.battery_level", data.get("battery_level"), 3)
    ck.eq("status.battery_charging", data.get("battery_charging"), False)
    ck.eq("status.drive", data.get("drive"), "UNKNOWN")
    ck.eq("status.mode", data.get("mode"), "P")
    ck.eq("status.hevc", data.get("hevc"), "off")
    ck.check("status.cameras 是数组", isinstance(data.get("cameras"), list), repr(data.get("cameras"))[:60])


def check_liveview(ck, mp, nv12):
    """两种取景：长度、布局不变量、帧是否在动、能否被 verify_nv12 解开。"""
    frames = {}
    for low, path, want_len in ((False, "/api/v1/liveview/get", HD_LEN),
                                (True, "/api/v1/liveview/get_low", LOW_LEN)):
        tag = "liveview_low" if low else "liveview_hd"
        status, headers, body = http_get(mp.url(path), timeout=20.0)
        if not ck.eq("%s HTTP 200" % tag, status, 200):
            return
        ck.eq("%s 字节数" % tag, len(body), want_len)
        # 低清：Y 平面 360x240 = 86400（宽高都减半），UV = 240 行 x 720 字节 = 172800
        # 高清：Y 平面 720x480 = 345600，UV 同样是 240 行 x 720 字节 = 172800
        y_w, y_h = (720 // 2, 480 // 2) if low else (720, 480)
        uv_bytes = (720 // 2) * 2 * (480 // 2)
        ck.check("%s 不变量 Y字节+UV字节 == len(body)" % tag,
                 y_w * y_h + uv_bytes == len(body),
                 "Y %dx%d=%d + UV %d = %d" % (y_w, y_h, y_w * y_h, uv_bytes, y_w * y_h + uv_bytes))
        ck.check("%s 与 expectedSize 一致" % tag,
                 (720 * 480 // 4 * 3 if low else 720 * 480 * 3 // 2) == len(body),
                 "expectedSize(720,480,low=%s)" % low)
        # 再取一帧，内容必须变化（左上角帧号方块 + 移动竖条）
        _, _, body2 = http_get(mp.url(path), timeout=20.0)
        ck.check("%s 两次取帧内容不同" % tag, body != body2, "%d vs %d 字节" % (len(body), len(body2)))
        frames[tag] = body
        if nv12 is not None:
            try:
                w, h, px = nv12.decode(body, low=low)
                ck.check("%s 能被 verify_nv12 解码" % tag,
                         (w, h) == (720, 480) and len(px) == 720 * 480 * 3, "输出 %dx%d" % (w, h))
            except Exception as exc:                       # noqa: BLE001
                ck.check("%s 能被 verify_nv12 解码" % tag, False, "%s: %s" % (type(exc).__name__, exc))
    return frames


def check_dirlist(ck, mp):
    status, headers, body = http_get(mp.url("/cgi-bin/dirlist?p=/", cgi=True))
    ck.eq("dirlist HTTP 200", status, 200)
    ck.check("dirlist Content-Type 是 JSON", "json" in headers.get("content-type", ""),
             headers.get("content-type", ""))
    try:
        root = json.loads(body.decode("utf-8"))
    except Exception as exc:                               # noqa: BLE001
        ck.check("dirlist 根目录可解析", False, "%s: %s" % (type(exc).__name__, exc))
        return
    ck.check("dirlist 是数组", isinstance(root, list), "len=%d" % (len(root) if isinstance(root, list) else -1))
    names = {e.get("name"): e for e in root if isinstance(e, dict)}
    ck.check("根目录含 DCIM 目录", names.get("DCIM", {}).get("dir") is True, repr(names.get("DCIM")))

    status, headers, body = http_get(mp.url("/cgi-bin/dirlist?p=/DCIM/100PHOTO", cgi=True))
    ck.eq("dirlist 子目录 HTTP 200", status, 200)
    entries = json.loads(body.decode("utf-8"))
    by_name = {e.get("name"): e for e in entries if isinstance(e, dict)}
    jpg = by_name.get("SAM_0001.JPG")
    ck.check("dirlist 能解析出文件名+大小", bool(jpg) and isinstance(jpg.get("size"), int) and jpg["size"] > 0,
             repr(jpg))
    ck.eq("SAM_0001.JPG size", (jpg or {}).get("size"), 4194304)
    ck.check("同目录有 SAM_0001.SRW（RAW）", "SAM_0001.SRW" in by_name, "共 %d 项" % len(entries))

    status, headers, body = http_get(mp.url("/cgi-bin/dirlist?p=/MP_ROOT/100VIDEO", cgi=True))
    vids = [e for e in json.loads(body.decode("utf-8")) if str(e.get("name", "")).endswith(".MP4")]
    ck.check("视频目录能列出 MP4", len(vids) >= 1, repr([e.get("name") for e in vids]))

    # 不存在的目录：真机同样给 200 + text/plain，不是 JSON
    status, headers, body = http_get(mp.url("/cgi-bin/dirlist?p=/NOPE", cgi=True))
    ck.eq("dirlist 不存在路径 HTTP 200", status, 200)
    ck.check("dirlist 不存在路径返回 text/plain", "text/plain" in headers.get("content-type", ""),
             headers.get("content-type", ""))


def check_thumb(ck, mp):
    # 1) 正常 JPG -> 真 JPEG
    status, headers, body = http_get(mp.url("/cgi-bin/thumb?f=SAM_0001.JPG&w=320&q=80", cgi=True))
    ck.eq("thumb JPG HTTP 200", status, 200)
    ck.check("thumb JPG Content-Type=image/jpeg", headers.get("content-type", "").startswith("image/jpeg"),
             headers.get("content-type", ""))
    ck.check("thumb JPG 是 JPEG magic (FF D8)", body[:2] == b"\xff\xd8", body[:4].hex())
    ck.check("thumb JPG 以 FFD9 结束", body[-2:] == b"\xff\xd9", body[-2:].hex())
    ck.check("thumb JPG 是真实 JPEG 长度", len(body) > 200, "%d 字节" % len(body))
    ck.eq("thumb JPG Content-Length 与实际一致",
          headers.get("content-length"), str(len(body)))

    # 2) RAW(.SRW) -> 真机行为：HTTP 200 + text/plain + 30 字节错误文本（最大的坑）
    status, headers, body = http_get(mp.url("/cgi-bin/thumb?f=SAM_0001.SRW&w=320&q=80", cgi=True))
    ck.eq("thumb SRW 竟然是 HTTP 200（陷阱）", status, 200)
    ck.check("thumb SRW Content-Type=text/plain", "text/plain" in headers.get("content-type", ""),
             headers.get("content-type", ""))
    ck.eq("thumb SRW body 长度 30", len(body), 30)
    ck.check("thumb SRW body 不是 JPEG", body[:2] != b"\xff\xd8", body[:2].hex())
    ck.check("thumb SRW body 内容逐字一致", body == THUMB_ERROR, repr(body))

    # 3) 不存在的文件 -> 同样的 200 + text/plain
    status, headers, body = http_get(mp.url("/cgi-bin/thumb?f=NOPE_9999.JPG&w=320&q=80", cgi=True))
    ck.eq("thumb 不存在文件 HTTP 200", status, 200)
    ck.check("thumb 不存在文件返回错误文本", body == THUMB_ERROR and "text/plain" in headers.get("content-type", ""),
             repr(body))
    # 4) 结论：判帧只能靠 Content-Type / JFIF magic，不能靠状态码
    ck.check("判帧必须看 Content-Type/magic（状态码不足以区分）", True,
             "200 既可能是 JPEG 也可能是 30 字节错误文本")


def check_shutter(ck, mp):
    """
    快门语义：一次快门写 2 个文件；S1(Super_L) 按住期间点 S2(Super_R) 才拍照。
    """
    status, _, body = http_get(mp.url("/__mock__/files"))
    files_before = len(json.loads(body.decode("utf-8"))["files"])

    status, _, _ = http_get(mp.url("/api/v1/shutter/normal"))
    ck.eq("shutter/normal HTTP 200", status, 200)

    status, _, body = http_get(mp.url("/__mock__/shutter_log"))
    log = json.loads(body.decode("utf-8"))
    last = log[-1] if log else {}
    ck.check("shutter/normal 记录为已拍照", last.get("captured") is True, repr(last)[:100])
    ck.eq("一次快门写 2 个文件（JPG+SRW）", len(last.get("files") or []), 2)

    status, _, body = http_get(mp.url("/__mock__/files"))
    files_after = len(json.loads(body.decode("utf-8"))["files"])
    ck.eq("文件数差值 == 2（不能用差值判断张数）", files_after - files_before, 2)

    status, _, _ = http_get(mp.url("/api/v1/shutter/silent"))
    ck.eq("shutter/silent HTTP 200", status, 200)

    # 真机时序：keydown(S1) -> 0.45s -> key(S2) -> 1.0s -> keyup(S1)
    http_get(mp.url("/__mock__/reset"))
    http_get(mp.url("/api/v1/input/inject?keydown=Super_L"))
    time.sleep(0.05)
    http_get(mp.url("/api/v1/input/inject?key=Super_R"))
    time.sleep(0.05)
    http_get(mp.url("/api/v1/input/inject?keyup=Super_L"))
    _, _, body = http_get(mp.url("/__mock__/shutter_log"))
    log = json.loads(body.decode("utf-8"))
    ck.check("S1 按住期间点 S2 -> 拍照", len(log) == 1 and log[0].get("captured") is True, repr(log)[:120])

    # 先松 S1（或根本没按）再点 S2 -> 只对焦
    http_get(mp.url("/__mock__/reset"))
    http_get(mp.url("/api/v1/input/inject?key=Super_R"))
    _, _, body = http_get(mp.url("/__mock__/shutter_log"))
    log = json.loads(body.decode("utf-8"))
    ck.check("没有 S1 就点 S2 -> 只对焦不拍照",
             len(log) == 1 and log[0].get("captured") is False, repr(log)[:120])

    # 参数校验
    status, _, _ = http_get(mp.url("/api/v1/input/inject"))
    ck.eq("inject 缺参数 -> 400", status, 400)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="验证 mock_camera.py 是否符合真机契约")
    ap.add_argument("--python", default=sys.executable, help="用哪个解释器跑 mock_camera.py")
    ap.add_argument("--port", type=int, default=0, help="指定 daemon 端口（默认随机挑一对相邻空闲端口）")
    ap.add_argument("--host", default="127.0.0.1", help="mock 监听地址（默认只绑回环，避免触发防火墙弹窗）")
    ap.add_argument("--verbose", action="store_true", help="打印 mock 的日志")
    args = ap.parse_args(argv)

    if not os.path.exists(MOCK):
        print("找不到 %s" % MOCK)
        return 2

    print("=== 验证 mock_camera.py（真机契约）===")
    ck = Checker()
    nv12 = None
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import verify_nv12 as nv12                     # 顺便做一次交叉验证
    except Exception as exc:                           # noqa: BLE001
        print("note: 无法 import verify_nv12（%s），跳过解码交叉验证" % exc)

    mp = None
    try:
        for attempt in range(3):
            base = args.port or pick_ports()
            if not base:
                print("找不到可用端口")
                return 2
            mp = MockProcess(args.python, base, host=args.host, verbose=args.verbose).start()
            print("启动: %s" % " ".join(mp.cmd))
            ok, note = mp.wait_ready(timeout=20.0)
            if ok:
                print("就绪: %s" % note)
                break
            print("第 %d 次启动失败: %s" % (attempt + 1, note))
            for line in mp.log_tail(20):
                print("  mock| %s" % line)
            mp.stop()
            mp = None
            if args.port:
                break
        if mp is None:
            print("mock 启动失败")
            return 2

        ck.check("mock 起来并能响应", True, "daemon=%d cgi=%d" % (mp.port, mp.cgi_port))

        check_info(ck, mp)
        check_status(ck, mp)
        check_liveview(ck, mp, nv12)
        check_dirlist(ck, mp)
        check_thumb(ck, mp)
        check_shutter(ck, mp)
    finally:
        if mp is not None:
            if args.verbose or ck.failed:
                for line in mp.log_tail(40):
                    print("  mock| %s" % line)
            mp.stop()

    total = ck.passed + ck.failed
    print("--- %d/%d 通过%s%s" % (
        ck.passed, total,
        "，%d 项失败" % ck.failed if ck.failed else "",
        "，%d 项跳过" % ck.skipped if ck.skipped else "",
    ))
    return 1 if ck.failed else 0


if __name__ == "__main__":
    sys.exit(main())
