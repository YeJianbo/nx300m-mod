#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_deep_diag.py —— 按键注入失败的**动态库依赖链**深入诊断

背景
----
daemon 提供的 `GET /api/v1/input/inject?...` 接口，不管怎么调都返回 200，
但相机毫无反应。于是怀疑它内部调用的 `nx-input-injector` 起不来。
本脚本就是把这条件依赖链一层层剥开。

诊断步骤
--------
1. 定位 `nx-input-injector`（试 /opt/usr/nx-ks/nx-rc/tools/usr/bin/）
2. `ldd` 它，看缺哪个库
3. 看 /opt/usr/nx-ks/nx-rc/tools/usr/lib/ 下有哪些 .so
4. 看 daemon 进程的 LD_LIBRARY_PATH：
       cat /proc/<pid>/environ | tr '\\0' '\\n' | grep LD_LIBRARY
5. 手动带上 LD_LIBRARY_PATH 再跑一次，捕获新的报错
6. `readelf -sW <libxkbcommon.so.0> | grep GLIBC_2.17` 确认符号版本需求
7. `ldd --version` 打印本机 glibc

实测结论（**这是相机端 ABI 问题，App 侧无解**）
-----------------------------------------------
* 第一级问题：`nx-input-injector` 缺 `libxdo.so.3`。
  该库其实**就在**相机上：
      /opt/usr/nx-ks/nx-rc/tools/usr/lib/libxdo.so.3
  但它**不在 LD_LIBRARY_PATH 里**，所以 loader 找不到。
* 第二级问题（硬门槛）：手动补上 LD_LIBRARY_PATH 后，
  换成 `libxkbcommon.so.0` 报错，它要求 **GLIBC_2.17**，
  而相机上的 glibc 是 **2.13**。
* 也就是说：即使把第一级依赖修好，第二级也过不去 ——
  这是**编译时的 ABI 门槛**，无法通过配置绕过（除非重编那批库或静态链接新工具）。
* 顺带确认：**XTEST 扩展本来就有**（`Initializing built-in extension XTEST`），
  `libXtst.so.6` / `libX11.so.6.3.0` 也都在 /usr/lib，
  所以问题**不在 XTEST**，不要往那个方向重复排查（见 camera_xtest_check.py）。

诊断输出落盘位置
----------------
FTP 只暴露 SD 卡根 `/opt/storage/sdcard`，`/tmp` 与 `/var/log` 取不回，
所以诊断输出一律写 `/mnt/mmc/`（= SD 卡挂载点），再 FTP 取回。

telnet 两个必踩坑
-----------------
1. 必须过滤 IAC 协商字节（WILL/WONT/DO/DONT 吞 3 字节，其余吞 2），并开启回显。
2. 提示符必须用行尾正则 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（re.M）；
   不能用 `"# " in buf` —— 后续提示符 `[root@drime5 ~]#` **没有尾随空格**。
   剥提示符前先去 ANSI 颜色码。
"""

import ftplib
import io
import re
import socket
import sys

CAMERA_IP = "192.168.5.39"
TELNET_PORT = 23
FTP_PORT = 21
TELNET_USER, TELNET_PASS = "root", ""
FTP_USER, FTP_PASS = "root", ""

TOOLS_BIN = "/opt/usr/nx-ks/nx-rc/tools/usr/bin"
TOOLS_LIB = "/opt/usr/nx-ks/nx-rc/tools/usr/lib"
INJECTOR = TOOLS_BIN + "/nx-input-injector"

REMOTE_OUT = "/mnt/mmc/deep_diag.txt"
FTP_OUT = "deep_diag.txt"

PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    return PROMPT_RE.search(strip_ansi(text)) is not None


def decode_telnet(raw: bytes) -> str:
    """过滤 IAC：WILL/WONT/DO/DONT(0xFB..0xFE) 吞 3 字节，其余吞 2 字节。"""
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


def telnet_run(commands, host=CAMERA_IP, port=TELNET_PORT, timeout=15.0):
    """登录 telnet 依次执行命令，返回 [(命令, 输出)]。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = ""

    def pump(quiet=False):
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                break                    # 超时不清空已读缓冲
            if not chunk:
                break
            buf += decode_telnet(chunk)
            if not quiet and looks_like_prompt(buf):
                break
        return buf

    def send(line):
        sock.sendall((line + "\n").encode("latin-1", "ignore"))

    pump(quiet=True)
    send("")
    pump(quiet=True)

    results = []
    for cmd in commands:
        buf = ""
        send(cmd)
        pump()
        text = strip_ansi(buf)
        lines = [ln for ln in text.splitlines() if ln.strip() != cmd.strip()]
        results.append((cmd, "\n".join(lines).strip()))

    try:
        sock.close()
    except OSError:
        pass
    return results


def ftp_fetch(remote_name, host=CAMERA_IP, port=FTP_PORT,
              user=FTP_USER, password=FTP_PASS):
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=15)
    ftp.login(user, password)
    buf = io.BytesIO()
    try:
        ftp.retrbinary("RETR " + remote_name, buf.write)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return buf.getvalue()


def build_script():
    """相机端诊断片段；输出全部追加到 /mnt/mmc/deep_diag.txt。"""
    return [
        "echo '=== camera_deep_diag: nx-input-injector 依赖链 ==='",
        "date",
        "> %s" % REMOTE_OUT,

        'echo "########## 0. injector 定位 ##########" >> %s' % REMOTE_OUT,
        "ls -l %s >> %s 2>&1" % (INJECTOR, REMOTE_OUT),
        "ls -l %s/ >> %s 2>&1" % (TOOLS_BIN, REMOTE_OUT),
        'echo "--- which nx-input-injector ---" >> %s' % REMOTE_OUT,
        "which nx-input-injector >> %s 2>&1" % REMOTE_OUT,

        'echo "########## 1. ldd nx-input-injector ##########" >> %s' % REMOTE_OUT,
        "ldd %s >> %s 2>&1" % (INJECTOR, REMOTE_OUT),

        'echo "########## 2. tools/usr/lib 下的 .so ##########" >> %s' % REMOTE_OUT,
        "ls -l %s/*.so* >> %s 2>&1" % (TOOLS_LIB, REMOTE_OUT),

        'echo "########## 3. daemon 进程与 LD_LIBRARY_PATH ##########" >> %s' % REMOTE_OUT,
        "ps | grep -i -E 'remote-controller|nx-rc|daemon' | grep -v grep >> %s 2>&1" % REMOTE_OUT,
        # /proc/<pid>/environ 是 \0 分隔的；tr 成换行再 grep
        "for p in $(pidof nx-remote-controller-daemon 2>/dev/null) "
        "$(ps | grep -i remote-controller | grep -v grep | awk '{print $1}'); do "
        'echo "--- pid=$p ---" >> %s; '
        "cat /proc/$p/environ 2>/dev/null | tr '\\0' '\\n' | grep -E 'LD_LIBRARY_PATH|PATH=' "
        ">> %s 2>&1; done" % (REMOTE_OUT, REMOTE_OUT, REMOTE_OUT),

        'echo "########## 4. 手动带 LD_LIBRARY_PATH 再跑一次 ##########" >> %s' % REMOTE_OUT,
        "LD_LIBRARY_PATH=%s:%s %s --help >> %s 2>&1" % (TOOLS_LIB, "$LD_LIBRARY_PATH",
                                                       INJECTOR, REMOTE_OUT),
        "LD_LIBRARY_PATH=%s %s >> %s 2>&1" % (TOOLS_LIB, INJECTOR, REMOTE_OUT),
        # 再 ldd 一次（带路径），看还剩哪个库缺
        "LD_LIBRARY_PATH=%s ldd %s >> %s 2>&1" % (TOOLS_LIB, INJECTOR, REMOTE_OUT),

        'echo "########## 5. libxkbcommon.so.0 的 GLIBC_2.17 符号需求 ##########" >> %s' % REMOTE_OUT,
        "ls -l %s/libxkbcommon.so* >> %s 2>&1" % (TOOLS_LIB, REMOTE_OUT),
        "readelf -sW %s/libxkbcommon.so.0 2>/dev/null | grep -c GLIBC_2.17 >> %s 2>&1"
        % (TOOLS_LIB, REMOTE_OUT),
        "readelf -sW %s/libxkbcommon.so.0 2>/dev/null | grep GLIBC_2.17 | head -20 >> %s 2>&1"
        % (TOOLS_LIB, REMOTE_OUT),
        'echo "--- 对照：库自己声明的版本需求 ---" >> %s' % REMOTE_OUT,
        "readelf -V %s/libxkbcommon.so.0 2>/dev/null | grep -A2 'Version needs' >> %s 2>&1"
        % (TOOLS_LIB, REMOTE_OUT),

        'echo "########## 6. 本机 glibc 版本 ##########" >> %s' % REMOTE_OUT,
        "ldd --version >> %s 2>&1" % REMOTE_OUT,
        "ls -l /lib/libc.so.6 /lib/libc-*.so >> %s 2>&1" % REMOTE_OUT,
        "ls /lib/ | grep -i 'libc-' >> %s 2>&1" % REMOTE_OUT,

        'echo "########## 7. 其它库路径 ##########" >> %s' % REMOTE_OUT,
        "ls -l /usr/lib/libxdo* /usr/lib/libxkbcommon* /usr/lib/libXtst* "
        "/usr/lib/libX11* >> %s 2>&1" % REMOTE_OUT,
        'echo "########## END ##########" >> %s' % REMOTE_OUT,
    ]


def main():
    print("[1/2] telnet 执行依赖链诊断 ...")
    try:
        telnet_run(build_script())
    except Exception as exc:                      # noqa: BLE001
        print("      telnet 阶段失败：%r" % (exc,))

    print("[2/2] FTP 取回 %s ..." % REMOTE_OUT)
    try:
        data = ftp_fetch(FTP_OUT)
    except Exception as exc:                      # noqa: BLE001
        print("      FTP 取回失败：%r" % (exc,))
        return 1

    text = data.decode("latin-1", "replace")
    with open(FTP_OUT, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(text)
    print("      取回 %d 字节 -> 本地 %s" % (len(data), FTP_OUT))

    # 就地高亮关键行，省得回看整份文件
    print("-" * 68)
    print("关键行：")
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("libxdo", "libxkbcommon", "glibc_2.1",
                                  "not found", "ld_library_path", "version")):
            print("  | " + line.strip()[:150])
    print("-" * 68)

    print("=" * 68)
    print("结论：**两级依赖问题，第二级是硬性 ABI 门槛，无法绕过。**")
    print("  级别 1：nx-input-injector 缺 libxdo.so.3。库其实在")
    print("          %s/libxdo.so.3" % TOOLS_LIB)
    print("          但不在 LD_LIBRARY_PATH 里 -> loader 找不到。")
    print("  级别 2：显式补上 LD_LIBRARY_PATH 后，报错换成 libxkbcommon.so.0")
    print("          要求 GLIBC_2.17，而相机 glibc 只有 2.13。")
    print("          => 编译期 ABI 门槛，配置无法绕过（除重编/静态链接）。")
    print("  补充：XTEST 扩展本来就有，libXtst.so.6 / libX11.so.6.3.0 也在 /usr/lib，")
    print("        所以问题不在 XTEST（详见 camera_xtest_check.py）。")
    print("  定性：**这是相机端 ABI 问题，App 侧无解。**")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
