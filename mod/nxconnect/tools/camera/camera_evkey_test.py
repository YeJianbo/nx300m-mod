#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_evkey_test.py —— 测试"直接写 /dev/input/event* 注入按键"这条路
（对应 camera_tools/evkey.c）

思路
----
按键注入失败的两个根因都在**用户态库**上：
    nx-input-injector -> libxdo.so.3（路径问题）
                      -> libxkbcommon.so.0（要求 GLIBC_2.17，相机只有 2.13）
但注入按键**根本不需要 X**：Linux 的输入子系统允许 root 直接往
/dev/input/eventX 写 struct input_event，内核就会把它当成真实按键事件。
而且相机上没有 `sendevent`（busybox 未编入，见 camera_busybox_test.py），
所以只能自带一个小的 C 程序 —— 那就是 `camera_tools/evkey.c`。

本脚本做什么
------------
1. 先确认 `evkey` 二进制是否存在（预期**不存在**：
   主机上没有 ARM 交叉编译器，没编译出来）。
2. 若不存在 -> 打印清楚的原因说明，并给出**用 evkey.c 编译的完整步骤**
   （需要 arm-linux-gnueabihf 工具链，`-static` 链接以避开 glibc 版本问题）。
3. 若存在 -> 依次用不同键码（S1=133、S2=134、EV=181、OK=104）测试注入，
   每次都记录"注入前后目录里有没有多出新的 SAM 编号"来判断效果。
4. 无论哪种情况，都检查 /dev/input/event* 的权限（是否 root 可写）。

实测结论
--------
* **这条路未走通（缺交叉编译器），但理论上可行**：
  它不依赖 X，也不依赖那两个坏库，只要一个静态 ARM 二进制。
* /dev/input/event* 对 root 可写（root 身份运行即可）。
* 所以这是一条**值得以后再挖**的路：装上 ARM 工具链 -> 编译 evkey.c
  -> FTP 上传到 /mnt/mmc/ -> `cp` 到 /tmp 并 `chmod +x`（SD 卡可能 noexec）
  -> 直接跑，用 S1=133 / S2=134 注入快门。

计数约定（与其它脚本一致）
--------------------------
一次快门写 **JPG + SRW 两个文件**，所以判断效果**不能数文件个数**，
要按"去掉扩展名后的 SAM 编号"去重后做差集。

telnet 两个必踩坑（已处理）
---------------------------
1. 必须过滤 IAC 协商字节：WILL/WONT/DO/DONT 吞 3 字节，其余吞 2 字节，并开启回显。
2. 提示符必须用行尾正则 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（re.M），
   不能用 `"# " in buf` —— 后续提示符 `[root@drime5 ~]#` 没有尾随空格。
"""

import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
TELNET_USER, TELNET_PASS = "root", ""
CGI_PORT = 8080
CGI = "http://%s:%d" % (CAMERA_IP, CGI_PORT)
PHOTO_DIR = "/DCIM/100PHOTO"

# evkey 的可能位置：SD 卡（FTP 上传目标）与 /tmp（拷贝后执行）
EVKEY_CANDIDATES = [
    "/mnt/mmc/evkey",                 # SD 卡根，FTP 上传到这里
    "/tmp/evkey",                     # 拷过来执行（SD 卡可能 noexec）
    "/opt/usr/nx-ks/nx-rc/tools/usr/bin/evkey",
]

# 待测键码（NX500 实测键码）
TEST_KEYS = [
    (133, "S1 快门半按"),
    (134, "S2 快门全按"),
    (181, "EV 曝光补偿"),
    (104, "OK 确定"),
]

RAW_EXTENSIONS = (".jpg", ".jpeg", ".srw", ".srw2", ".png", ".mp4", ".mov")

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
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


class TelnetShell:
    def __init__(self, host=CAMERA_IP, port=TELNET_PORT, timeout=15.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = ""
        self._pump(quiet=True)
        self._send("")
        self._pump(quiet=True)

    def _pump(self, quiet=False):
        while True:
            try:
                chunk = self.sock.recv(8192)
            except socket.timeout:
                break                    # 超时保留已读缓冲
            if not chunk:
                break
            self.buf += decode_telnet(chunk)
            if not quiet and looks_like_prompt(self.buf):
                break
        return self.buf

    def _send(self, line):
        self.sock.sendall((line + "\n").encode("latin-1", "ignore"))

    def run(self, cmd, timeout=15.0):
        self.buf = ""
        self.sock.settimeout(timeout)
        self._send(cmd)
        self._pump()
        text = strip_ansi(self.buf)
        lines = [ln for ln in text.splitlines() if ln.strip() != cmd.strip()]
        return "\n".join(lines).strip()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 目录编号（避免"一次快门两个文件"的计数陷阱）
# ---------------------------------------------------------------------------
def http_get(url, timeout=10.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        except Exception:                          # noqa: BLE001
            return exc.code, b""
    except Exception:                              # noqa: BLE001
        return 0, b""


def shot_ids(names):
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


def photo_ids():
    """取当前目录的照片编号集合。"""
    status, body = http_get("%s/cgi-bin/dirlist?p=%s" % (CGI, PHOTO_DIR))
    if status != 200 or not body:
        return set()
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
        names = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict) and "name" in item:
                    names.append(str(item["name"]))
    except ValueError:
        names = re.findall(r'"([^"]+\.(?:JPG|SRW|jpg|srw))"', text)
    return shot_ids(names)


COMPILE_STEPS = r"""
用 evkey.c 编译的完整步骤（在**主机侧**做，相机上没编译器）：

  0) 原理：evkey.c 直接 open("/dev/input/eventX") 并 write() 若干
     struct input_event { timeval time; __u16 type; __u16 code; __s32 value; }，
     EV_KEY/按下=1/抬起=0，最后发一个 EV_SYN/SYN_REPORT 让内核立即派发。
     **完全不依赖 X、不依赖 libxdo / libxkbcommon**，所以绕开了那两个坏库。

  1) 准备 ARM 工具链（三选一）：
       a. WSL / Debian / Ubuntu（最省事）：
            sudo apt update && sudo apt install -y gcc-arm-linux-gnueabihf
       b. MSYS2（Windows 原生）：装 mingw-w64-x86_64-arm-none-eabi-gcc
            pacman -S mingw-w64-x86_64-arm-none-eabi-gcc
            （注意：bare-metal eabi 工具链没有 glibc，正好适合 -static）
       c. 官方 Linaro / ARM GNU Toolchain 的
          arm-linux-gnueabihf-* 交叉工具链，解压后把 bin/ 加进 PATH

  2) 编译（**必须 -static**）：
       arm-linux-gnueabihf-gcc -static -O2 -o evkey evkey.c

  3) 为什么必须静态链接：
       相机上是 glibc **2.13**（`ldd --version` 实测），
       而主机工具链默认带的 glibc 是 2.17+ / 2.3x。
       动态链接的话，运行时会出现
           version `GLIBC_2.17' not found
       —— 和 libxkbcommon.so.0 撞的是同一堵墙。
       静态链接把 libc 一起吃进二进制，彻底规避版本问题。
       （如果工具链没有 glibc 而只有 newlib，也直接 -static 即可。）

  4) 检查产物：
       file evkey          # 期望 "ELF 32-bit LSB executable, ARM, EABI5, statically linked"

  5) 上传并执行：
       curl.exe --user root: -T evkey ftp://192.168.5.39/evkey
       # SD 卡可能挂载成 noexec，所以先拷到 /tmp 再加执行位：
       #   cp /mnt/mmc/evkey /tmp/evkey && chmod +x /tmp/evkey
       # 用法（键码见 TEST_KEYS）：
       #   /tmp/evkey 0 133          # 往 event0 注入一次 S1
       #   /tmp/evkey 0 134          # 往 event0 注入一次 S2
       # 拍照要模拟机械两段式开关（和 App 的序列 A 一致）：
       #   /tmp/evkey 0 133 down; sleep 0.45; /tmp/evkey 0 134; sleep 1; /tmp/evkey 0 133 up
       # 注意：相机上没有 `timeout` 命令，别用它包命令。

  6) 验证是否有效：
       用 8080 CGI 的 /cgi-bin/dirlist?p=/DCIM/100PHOTO 看有没有多出新的
       **SAM 编号**（去掉扩展名后去重；一次快门写 JPG+SRW 两个文件，
       不能数文件个数）。
"""


def main():
    print("=" * 74)
    print("NX500 evkey（直接写 /dev/input）路线测试   相机=%s" % CAMERA_IP)
    print("=" * 74)

    sh = TelnetShell()

    # --- 1. 找 evkey + 设备权限 ---
    print("[1] 查找 evkey 二进制与 /dev/input 权限")
    print("-" * 74)
    found = None
    for path in EVKEY_CANDIDATES:
        out = sh.run("ls -l %s 2>&1" % path)
        print("   $ ls -l %s" % path)
        for line in out.splitlines()[:3]:
            print("     | " + line[:140])
        if "No such file" not in out and "cannot access" not in out and "evkey" in out:
            found = path

    perm = sh.run("ls -l /dev/input/ 2>&1; id")
    print("   $ ls -l /dev/input/ ; id")
    for line in perm.splitlines()[:12]:
        print("     | " + line[:140])

    # 顺便看看有没有 uinput（有的话可以造一个虚拟输入设备，另一条思路）
    uinput = sh.run("ls -l /dev/uinput 2>&1")
    print("   $ ls -l /dev/uinput")
    for line in (uinput.splitlines() or ["(无输出)"])[:2]:
        print("     | " + line[:140])

    # --- 2. 存在 / 不存在 两条分支 ---
    if not found:
        print("-" * 74)
        print("[2] evkey **不存在** —— 与预期一致")
        print("    原因：主机上没有 ARM 交叉编译器，当时没有编译出这个二进制。")
        print("    注意：相机上**没有 gcc**（也没有 python/perl），")
        print("          所以不可能在相机端现场编译（见 camera_busybox_test.py）。")
        print("    因此这条路**未走通，但理论上可行**：它不需要 X，")
        print("    也不需要 libxdo / libxkbcommon，只要一个静态 ARM 二进制。")
        print(COMPILE_STEPS)
        print("-" * 74)
        print("[3] /dev/input/event* 权限检查")
        writable = []
        for line in perm.splitlines():
            if line.startswith("c") and "event" in line:
                writable.append(line)
        for line in writable:
            parts = line.split()
            # 权限串形如 crw-rw---- / crw-r--r--
            mode = parts[0] if parts else ""
            owner_w = len(mode) > 2 and mode[2] == "w"
            print("   %s   root 可写=%s" % (line[:110], "是" if owner_w else "否"))
        print("   -> 以 root 身份运行时可写；脚本用 root 登录，这一条没问题。")
        sh.close()
        print("=" * 74)
        print("结论：**这条路未走通（缺交叉编译器），但理论上可行。**")
        print("      下一步：按上面步骤编译 evkey.c（-static！），")
        print("      FTP 上传 /mnt/mmc/evkey，再 cp 到 /tmp 并 chmod +x 执行。")
        print("=" * 74)
        return 0

    # --- evkey 存在：逐个键码实测 ---
    print("-" * 74)
    print("[2] 找到 evkey: %s —— 开始逐键码注入测试" % found)
    # SD 卡可能 noexec，先确保 /tmp 有一份可执行的
    print("   $ cp %s /tmp/evkey && chmod +x /tmp/evkey" % found)
    print("     | " + sh.run("cp %s /tmp/evkey && chmod +x /tmp/evkey; echo RC=$?" % found)[:200])
    exe = "/tmp/evkey"

    results = []
    for code, name in TEST_KEYS:
        print("-" * 74)
        print("   键码 %d (%s)" % (code, name))
        before = photo_ids()
        out = sh.run("%s 0 %d; echo RC=$?" % (exe, code))
        print("     $ %s 0 %d" % (exe, code))
        for line in out.splitlines()[:5]:
            print("     | " + line[:140])
        time.sleep(3.0)                       # >=3s 让相机写卡
        after = photo_ids()
        new_ids = after - before              # 编号去重后的差集
        results.append((code, name, sorted(new_ids)))
        print("     -> 新增 SAM 编号：%s"
              % (", ".join(sorted(new_ids)) if new_ids else "(无)"))

    # 再试一次序列 A 的组合（半按保持 -> 全按 -> 松半按）
    print("-" * 74)
    print("   组合测试：模拟序列 A（down 133 -> 450ms -> 134 -> 1000ms -> up 133）")
    before = photo_ids()
    out = sh.run("LD_LIBRARY_PATH= %s 0 133 down; sleep 0.45; %s 0 134; "
                 "sleep 1; %s 0 133 up; echo RC=$?" % (exe, exe, exe))
    for line in out.splitlines()[:6]:
        print("     | " + line[:140])
    time.sleep(3.0)
    after = photo_ids()
    combo_new = sorted(after - before)
    results.append(("A", "down133/134/up133", combo_new))
    print("     -> 新增 SAM 编号：%s" % (", ".join(combo_new) if combo_new else "(无)"))

    sh.close()
    print("=" * 74)
    print("汇总（新增 SAM 编号 = 去掉扩展名后去重的编号差集）")
    print("-" * 74)
    for code, name, new_ids in results:
        print("  %-6s %-16s %s" % (code, name, ", ".join(new_ids) if new_ids else "-"))
    print("-" * 74)
    print("结论：若组合测试出现新编号 -> 这条路**可行**，可以考虑把它作为")
    print("      App 的快门后备方案（注意：SD 卡 noexec，要先 cp 到 /tmp）。")
    print("      若全无新编号 -> 检查是否写对了 event 节点（S1/S2 在 event0，")
    print("      见 camera_keys_probe.py 的 capability 位图）。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
