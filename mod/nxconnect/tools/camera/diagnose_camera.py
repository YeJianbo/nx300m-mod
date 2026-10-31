#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnose_camera.py —— 早期综合诊断入口（把散落的检查汇成一个脚本）

这是最开始写的那一个"一键体检"脚本：连上相机，把每一层能力都试一遍，
每节给 ✅/❌ 与一句话结论，最后打一张汇总表。
后面那些专项脚本（时间、注入、evkey、busybox …）都是从这份体检里
发现问题之后拆出去的。

六个体检节
----------
1. 网络可达性：80 / 8080 / 21 / 23 四个端口的 TCP 连通性
2. daemon 身份：/api/v1/camera/info + /api/v1/camera/status
3. CGI 可用性：/cgi-bin/dirlist?p=/DCIM
4. 取景可用性：两次取 /api/v1/liveview/get，打印长度与耗时
5. shell 可达性：telnet 登录 + uname -a、ldd --version、df -h /mnt/mmc
6. 注入可用性：试一次 input/inject，并用目录列表判断是否有效果

⚠ 第 6 节的关键点
-----------------
**判断注入是否有效果，要看目录里是否多出新的 SAM 编号，
  而不是看 HTTP 状态码 —— 注入接口即使失败也返回 200。**
而且一次快门写 JPG + SRW 两个文件，所以**不能数文件个数**，
必须按"去掉扩展名后的 SAM 编号"去重后再做差集。

真机参数（全部实测）
--------------------
  相机 IP            192.168.5.39
  80                  nx-remote-controller-daemon（mongoose HTTP）
  8080                busybox httpd + CGI
                      （根目录 /opt/usr/nx-ks/nx-rc/capdtm/www，**不是 SD 卡**）
  21                  FTP（用户 root，**密码为空**，根 = SD 卡 /opt/storage/sdcard）
  23                  telnet（用户 root，**密码为空**）
  liveview/get        518400 字节
  liveview/get_low    259200 字节
  8080 缩略图         /cgi-bin/thumb?f=<name>&w=<64..1024>&q=<50..95>
                      失败时返回 **HTTP 200 + text/plain**（约 30 字节，
                      如 `thumb error: source not found`），必须校验 JPEG magic FF D8
  相机端缩略图由**单核 convert** 生成，**并发必须限制**（项目上限取 2）

已排除项（体检里顺带确认，避免重复排查）
----------------------------------------
* 相机上没有 `timeout` 命令（busybox 未编入）-> 脚本里绝不使用 timeout
* 按键注入失败是**相机端 ABI 问题**：
  libxdo.so.3 路径缺失（可修）+ libxkbcommon.so.0 要求 GLIBC_2.17
  而相机 glibc 只有 2.13（硬门槛）-> **App 侧无解**
* XTEST 本来就有（`Initializing built-in extension XTEST`），不是它的锅
"""

import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request

CAMERA_IP = "192.168.5.39"
PORT_API = 80        # nx-remote-controller-daemon
PORT_CGI = 8080      # busybox httpd + CGI
PORT_FTP = 21        # root / 空密码
PORT_TELNET = 23     # root / 空密码

API = "http://%s:%d" % (CAMERA_IP, PORT_API)
CGI = "http://%s:%d" % (CAMERA_IP, PORT_CGI)

PHOTO_DIR = "/DCIM/100PHOTO"
DCIM_DIR = "/DCIM"

KEY_S1 = 133
KEY_S2 = 134

LIVEVIEW_SIZE = 518400        # /api/v1/liveview/get
LIVEVIEW_LOW_SIZE = 259200    # /api/v1/liveview/get_low

RAW_EXTENSIONS = (".jpg", ".jpeg", ".srw", ".srw2", ".png", ".mp4", ".mov")

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    """提示符必须用行尾正则；不能用 `'# ' in buf`（后续提示符没有尾随空格）。"""
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
    """
    过滤 IAC 协商字节。
    一连上服务器就发 \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03
    并且开启回显。
    规则：遇 255(IAC) 读下一字节当命令；若命令在 0xFB..0xFE
    （WILL/WONT/DO/DONT）则吞 3 字节，否则吞 2 字节。
    """
    IAC = 255
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            break
        cmd = raw[i + 1]
        i += 3 if 0xFB <= cmd <= 0xFE else 2
    return out.decode("latin-1", "replace")


def tcp_check(host, port, timeout=3.0):
    """TCP 连通性；返回 (是否连通, 说明)。"""
    start = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, "%.0fms" % ((time.time() - start) * 1000)
    except Exception as exc:                       # noqa: BLE001
        return False, repr(exc)[:60]


def http_get(url, timeout=12.0):
    """GET，返回 (status, body, 耗时秒)。不抛异常。"""
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            return resp.getcode(), body, time.time() - start
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:                          # noqa: BLE001
            body = b""
        return exc.code, body, time.time() - start
    except Exception:                              # noqa: BLE001
        return 0, b"", time.time() - start


def is_jpeg(data: bytes) -> bool:
    """JPEG magic FF D8。8080 缩略图失败时会返回 HTTP 200 + text/plain，必须校验。"""
    return len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8


def dirlist(path):
    """取 8080 CGI 的目录列表，返回文件名列表。"""
    status, body, _ = http_get("%s/cgi-bin/dirlist?p=%s" % (CGI, path))
    if status != 200 or not body:
        return []
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        return re.findall(r'"([^"]+\.[A-Za-z0-9]{2,4})"', text)

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
    **计数陷阱的解法**：一次快门写 SAM_xxxx.JPG + SAM_xxxx.SRW 两个文件，
    去掉扩展名后都变成 SAM_xxxx -> set 去重后只算 1 张。
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


def section(no, title):
    print()
    print("=" * 72)
    print("第 %s 节  %s" % (no, title))
    print("-" * 72)


def verdict(ok, msg):
    print("   %s  %s" % ("✅" if ok else "❌", msg))
    return ok


# ---------------------------------------------------------------------------
# 1. 网络可达性
# ---------------------------------------------------------------------------
def sec1_network():
    section(1, "网络可达性（TCP 80 / 8080 / 21 / 23）")
    labels = {
        PORT_API: "nx-remote-controller-daemon (mongoose HTTP)",
        PORT_CGI: "busybox httpd + CGI",
        PORT_FTP: "FTP (root / 空密码)",
        PORT_TELNET: "telnet (root / 空密码)",
    }
    results = {}
    for port in (PORT_API, PORT_CGI, PORT_FTP, PORT_TELNET):
        ok, note = tcp_check(CAMERA_IP, port)
        results[port] = ok
        print("   %-5d %-44s %s  %s"
              % (port, labels[port], "✅" if ok else "❌", note))
    # 注意：相机上没有 timeout 命令，这里也绝不用 timeout 包装探测
    allok = all(results.values())
    verdict(allok, "四个端口%s" % ("全部可达" if allok else
                                  "有不可达项（见上表；80 不通则 App 核心功能不可用）"))
    return allok, results


# ---------------------------------------------------------------------------
# 2. daemon 身份
# ---------------------------------------------------------------------------
def sec2_daemon():
    section(2, "daemon 身份（/api/v1/camera/info + /api/v1/camera/status）")
    ok_info = ok_status = False

    status, body, dt = http_get("%s/api/v1/camera/info" % API)
    text = body.decode("utf-8", "replace").strip()
    print("   GET /api/v1/camera/info   -> HTTP %s  %.0fms" % (status, dt * 1000))
    print("     %s" % text[:300])
    if status == 200 and body:
        try:
            info = json.loads(text)
            print("     model=%s fw_ver=%s mac=%s"
                  % (info.get("model"), info.get("fw_ver"), info.get("mac_address")))
            ok_info = True
        except ValueError:
            ok_info = True                  # 有响应就算通，只是不是严格 JSON

    status, body, dt = http_get("%s/api/v1/camera/status" % API)
    text = body.decode("utf-8", "replace").strip()
    print("   GET /api/v1/camera/status -> HTTP %s  %.0fms" % (status, dt * 1000))
    print("     %s" % text[:300])
    if status == 200 and body:
        try:
            st = json.loads(text)
            print("     mode=%s battery_level=%s battery_percent=%s charging=%s hevc=%s"
                  % (st.get("mode"), st.get("battery_level"), st.get("battery_percent"),
                     st.get("battery_charging"), st.get("hevc")))
            if st.get("battery_percent") == -1:
                print("     注：battery_percent=-1 是常态，硬件不提供电量百分比"
                      "（`st bat get_soc` / `get_voltage` 都返回 ret=-1）")
            ok_status = True
        except ValueError:
            ok_status = True

    ok = ok_info and ok_status
    verdict(ok, "daemon %s（预期 model=NX500 / fw_ver=1.12）"
            % ("在线且两个接口都通" if ok else "有接口异常"))
    return ok, {}


# ---------------------------------------------------------------------------
# 3. CGI 可用性
# ---------------------------------------------------------------------------
def sec3_cgi():
    section(3, "CGI 可用性（/cgi-bin/dirlist?p=/DCIM）")
    status, body, dt = http_get("%s/cgi-bin/dirlist?p=%s" % (CGI, DCIM_DIR))
    text = body.decode("utf-8", "replace").strip()
    print("   GET /cgi-bin/dirlist?p=%s -> HTTP %s  %.0fms  %d 字节"
          % (DCIM_DIR, status, dt * 1000, len(body)))
    print("     %s" % text[:300])
    ok = status == 200 and bool(body)
    if ok:
        names = dirlist(DCIM_DIR)
        print("     解析出 %d 个条目：%s" % (len(names), ", ".join(names[:8])))

    # 顺带验证缩略图接口的"假成功"陷阱
    print("   附：缩略图接口 /cgi-bin/thumb 失败时返回 HTTP 200 + text/plain，")
    print("       所以必须校验 JPEG magic FF D8，而不是只看状态码。")
    status, body, dt = http_get("%s/cgi-bin/thumb?f=__not_exist__.JPG&w=256&q=80" % CGI)
    print("   故意取一个不存在的文件 -> HTTP %s, %d 字节, JPEG=%s -> %s"
          % (status, len(body), is_jpeg(body), "（预期：非 JPEG 的小文本）"
             if not is_jpeg(body) else "（意外成功）"))
    verdict(ok, "CGI %s（8080 的根是 /opt/usr/nx-ks/nx-rc/capdtm/www，不是 SD 卡）"
            % ("可用" if ok else "不可用"))
    return ok, {}


# ---------------------------------------------------------------------------
# 4. 取景可用性
# ---------------------------------------------------------------------------
def sec4_liveview():
    section(4, "取景可用性（两次 GET /api/v1/liveview/get）")
    sizes = []
    for i in (1, 2):
        status, body, dt = http_get("%s/api/v1/liveview/get" % API, timeout=20.0)
        sizes.append(len(body))
        print("   第 %d 次 -> HTTP %s  %d 字节  %.0fms  (预期 %d 字节)"
              % (i, status, len(body), dt * 1000, LIVEVIEW_SIZE))
    status_low, body_low, dt_low = http_get("%s/api/v1/liveview/get_low" % API, timeout=20.0)
    print("   get_low -> HTTP %s  %d 字节  %.0fms  (预期 %d 字节)"
          % (status_low, len(body_low), dt_low * 1000, LIVEVIEW_LOW_SIZE))
    ok = any(s == LIVEVIEW_SIZE for s in sizes) or len(body_low) == LIVEVIEW_LOW_SIZE
    verdict(ok, "取景%s（liveview/get=%d 字节，get_low=%d 字节）"
            % ("可用" if ok else "拿到的长度不对",
               LIVEVIEW_SIZE, LIVEVIEW_LOW_SIZE))
    return ok, {}


# ---------------------------------------------------------------------------
# 5. shell 可达性
# ---------------------------------------------------------------------------
def sec5_shell():
    section(5, "shell 可达性（telnet 登录 + uname / ldd --version / df）")
    try:
        sock = socket.create_connection((CAMERA_IP, PORT_TELNET), timeout=8.0)
    except Exception as exc:                       # noqa: BLE001
        verdict(False, "telnet 连接失败：%r" % (exc,))
        return False, {}

    sock.settimeout(10.0)
    buf = ""

    def pump(quiet=False):
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                break                    # 关键：超时不能清空已读缓冲
            if not chunk:
                break
            buf += decode_telnet(chunk)
            if not quiet and looks_like_prompt(buf):
                break
        return buf

    def send(line):
        sock.sendall((line + "\n").encode("latin-1", "ignore"))

    # 一连上就会收到 IAC 协商（\\xff\\xfd\\x01 等），decode_telnet 负责过滤
    pump(quiet=True)
    send("")
    pump(quiet=True)

    outputs = {}
    for cmd in ("uname -a", "ldd --version | head -2", "df -h /mnt/mmc", "date"):
        buf = ""
        send(cmd)
        pump()
        text = strip_ansi(buf)
        lines = [ln for ln in text.splitlines() if ln.strip() != cmd.strip()]
        out = "\n".join(lines).strip()
        outputs[cmd] = out
        print("   $ %s" % cmd)
        for line in (out.splitlines() or ["(无输出)"])[:5]:
            print("     | " + line[:140])

    try:
        sock.close()
    except OSError:
        pass

    ok = bool(outputs.get("uname -a"))
    verdict(ok, "shell %s（root / 空密码；提示符 [root@drime5 ~]# 无尾随空格，"
                "所以用行尾正则匹配）" % ("可达" if ok else "不可达"))
    print("   注：相机上没有 `timeout` 命令（busybox 未编入），脚本里不要用它。")
    return ok, outputs


# ---------------------------------------------------------------------------
# 6. 注入可用性
# ---------------------------------------------------------------------------
def sec6_inject():
    section(6, "注入可用性（input/inject + 目录编号差集判断）")
    print("   ⚠ 判断标准：**看目录里是否多出新的 SAM 编号**，")
    print("      而不是看 HTTP 状态码 —— 注入接口即使失败也返回 200。")

    before_files = dirlist(PHOTO_DIR)
    before_ids = shot_ids(before_files)
    print("   注入前：%d 个文件 / %d 个 SAM 编号" % (len(before_files), len(before_ids)))

    # 试一次序列 A（App 里唯一可行的快门序列）：
    #   keydown(S1) -> 450ms -> key(S2) -> 1000ms -> keyup(S1)
    seq = [("keydown=%d" % KEY_S1, 0.45), ("key=%d" % KEY_S2, 1.0),
           ("keyup=%d" % KEY_S1, 0.0)]
    for query, wait in seq:
        status, body, dt = http_get("%s/api/v1/input/inject?%s" % (API, query))
        print("   GET /api/v1/input/inject?%-14s -> HTTP %s  %.0fms  %s"
              % (query, status, dt * 1000, body.decode("utf-8", "replace")[:60]))
        if wait:
            time.sleep(wait)

    time.sleep(3.0)                      # >=3s 让相机写卡并回到可拍状态

    after_files = dirlist(PHOTO_DIR)
    after_ids = shot_ids(after_files)
    new_ids = after_ids - before_ids      # **按编号去重后的差集**，不是数文件个数
    print("   注入后：%d 个文件 / %d 个 SAM 编号" % (len(after_files), len(after_ids)))
    print("   新增 SAM 编号 %d 个：%s"
          % (len(new_ids), ", ".join(sorted(new_ids)) if new_ids else "(无)"))
    print("   （文件级新增 %d 个，含 JPG+SRW 两份，仅供参考，不作为张数依据）"
          % len(set(after_files) - set(before_files)))

    ok = bool(new_ids)
    if ok:
        verdict(True, "注入**有效**：拍到新编号 %s" % ", ".join(sorted(new_ids)))
    else:
        verdict(False, "注入**无效果**（接口仍返回 200，这正是坑）")
        print("   已知原因（相机端 ABI 问题，App 侧无解）：")
        print("     * nx-input-injector 缺 libxdo.so.3（库在 "
              "/opt/usr/nx-ks/nx-rc/tools/usr/lib/ 但不在 LD_LIBRARY_PATH）")
        print("     * 补上路径后又撞 libxkbcommon.so.0 要求 GLIBC_2.17，"
              "而相机 glibc 只有 2.13")
        print("     * XTEST 本来就有，不是它的锅；busybox 也没有 sendevent")
        print("     * 备选路线：交叉编译静态 ARM evkey 直接写 /dev/input/event*")
        print("       （见 camera_evkey_test.py / build_evkey.py）")
    return ok, {"new_ids": sorted(new_ids)}


def main():
    print("#" * 72)
    print("# NX500 综合诊断  相机=%s" % CAMERA_IP)
    print("# 端口：80 daemon / 8080 CGI / 21 FTP(root,空密码) / 23 telnet(root,空密码)")
    print("#" * 72)

    summary = []

    ok, _ = sec1_network()
    summary.append(("1 网络", ok, "四端口 TCP 连通性"))
    if not ok and not tcp_check(CAMERA_IP, PORT_API)[0]:
        print()
        print("80 端口不通 -> 后面几节多半也会失败；仍继续尝试以给出完整体检报告。")

    ok, _ = sec2_daemon()
    summary.append(("2 daemon", ok, "/api/v1/camera/info + status"))

    ok, _ = sec3_cgi()
    summary.append(("3 CGI", ok, "/cgi-bin/dirlist（8080）"))

    ok, _ = sec4_liveview()
    summary.append(("4 取景", ok, "liveview/get 518400 / get_low 259200 字节"))

    ok, _ = sec5_shell()
    summary.append(("5 shell", ok, "telnet uname / ldd --version / df"))

    ok, _ = sec6_inject()
    summary.append(("6 注入", ok, "看目录里有没有新增 SAM 编号（不是看 HTTP 码）"))

    print()
    print("=" * 72)
    print("汇总表")
    print("-" * 72)
    print("%-10s %-6s %s" % ("检查项", "结果", "说明"))
    print("-" * 72)
    for name, ok, note in summary:
        print("%-10s %-6s %s" % (name, "✅" if ok else "❌", note))
    print("-" * 72)
    passed = sum(1 for _, ok, _ in summary if ok)
    print("通过 %d / %d 项" % (passed, len(summary)))
    print("-" * 72)
    print("必须记住的既有结论：")
    print("  * 相机上没有 `timeout` 命令，任何脚本都不要用它")
    print("  * FTP 只暴露 SD 卡根；诊断输出一律写 /mnt/mmc/ 再取回")
    print("  * 一次快门写 JPG+SRW 两个文件 -> 判断张数必须按去掉扩展名的 SAM 编号去重")
    print("  * 注入接口失败也返回 200 -> 只能靠目录编号判断效果")
    print("  * 按键注入失败是相机端 ABI 问题（GLIBC_2.17 vs 2.13），App 侧无解；")
    print("    XTEST 本来就有，不要再往那个方向排查")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
