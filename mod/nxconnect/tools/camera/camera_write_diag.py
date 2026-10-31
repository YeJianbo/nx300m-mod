#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_write_diag.py —— 排查"往相机哪里写诊断文件能被取回"

动机
----
做注入排查时需要把相机端的诊断输出带回来。第一反应是写 /tmp，
结果 FTP 取不回来 —— 于是做了这个脚本把几个候选位置逐一验证。

做法
----
1. 通过 telnet 依次往下面四个位置各写一个测试文件，打印是否成功：
       /tmp/
       /var/log/
       /mnt/mmc/
       /opt/storage/sdcard/
2. 对每个位置，用 FTP（等价于命令行的
       curl.exe --user root: -o <本地> ftp://<ip>/<相对路径>
   ）尝试取回，打印 FTP 返回码与取回字节数。

实测结论
--------
* **FTP 只提供 SD 卡根**（= `/opt/storage/sdcard`，也就是 `/mnt/mmc` 指向的地方）。
  `/tmp` 与 `/var/log` **取不回**（FTP 根之下不存在这两个目录）。
* 所以：**诊断输出一律写 `/mnt/mmc/`**，再用
      curl.exe --user root: -o <本地> ftp://192.168.5.39/<文件名>
  取回。（FTP 用户 root，**密码为空**。）

命令行的等价取回方式（脚本里用 ftplib 实现，这里备注给人看）：
    curl.exe --user root: -o deep_diag.txt ftp://192.168.5.39/deep_diag.txt
注意 `root:` 后面那个冒号不能省 —— 它是"空密码"的表示法。

telnet 两个必踩坑
-----------------
1. 必须过滤 IAC 协商字节：一连上服务器就发
   \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03，并且开启回显。
   规则：遇 255(IAC) 读下一字节当命令；若命令在 0xFB..0xFE
   （WILL/WONT/DO/DONT）则吞 3 字节，否则吞 2 字节。
2. 提示符必须用行尾正则 `(\\[[^\\]]*\\]\\s*)?#\\s*$`（re.M），
   不能用 `"# " in buf` —— 后续提示符 `[root@drime5 ~]#` 没有尾随空格。
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
FTP_USER, FTP_PASS = "root", ""

MARKER = "NXWRITE-DIAG-OK"        # 写进测试文件的内容，便于验证取回的是哪个文件
STAMP = "2025-01-01T00:00:00"     # 固定串，避免依赖相机那台不准的时钟

# 候选写入位置： (标签, 绝对路径, FTP 相对路径 or None)
CANDIDATES = [
    ("/tmp",                    "/tmp/nxwrite_probe.txt",              None),
    ("/var/log",                "/var/log/nxwrite_probe.txt",          None),
    ("/mnt/mmc (SD 卡挂载点)",  "/mnt/mmc/nxwrite_probe.txt",          "nxwrite_probe.txt"),
    ("/opt/storage/sdcard",     "/opt/storage/sdcard/nxwrite_probe.txt", "nxwrite_probe.txt"),
]

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


def telnet_run(commands, host=CAMERA_IP, port=TELNET_PORT, timeout=12.0):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = ""

    def pump(quiet=False):
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                break                     # 超时不清空已读缓冲
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


def ftp_fetch_size(remote_rel, host=CAMERA_IP, port=FTP_PORT,
                   user=FTP_USER, password=FTP_PASS):
    """
    尝试从 FTP 根下按相对路径取回文件。
    返回 (是否成功, 字节数, 说明)。失败时说明里带 FTP 错误码。
    """
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, port, timeout=15)
        ftp.login(user, password)
    except Exception as exc:                      # noqa: BLE001
        return False, 0, "FTP 连接/登录失败: %r" % (exc,)

    buf = io.BytesIO()
    try:
        ftp.retrbinary("RETR " + remote_rel, buf.write)
        return True, len(buf.getvalue()), "OK"
    except ftplib.error_perm as exc:
        # 550 = 文件不存在/无权限 —— 这就是 /tmp、/var/log 的表现
        return False, 0, "FTP error_perm: %s" % (exc,)
    except Exception as exc:                      # noqa: BLE001
        return False, 0, "FTP 取回异常: %r" % (exc,)
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:                     # noqa: BLE001
                pass


def build_script():
    lines = ["echo '=== camera_write_diag: 诊断文件可写/可取回位置 ==='", "date"]
    for label, path, _rel in CANDIDATES:
        lines.append("echo '---------- %s ----------'" % label)
        # 写入并立刻验证；stderr 一起收，读只读文件系统时会在这里报错
        lines.append('echo "%s %s" > %s 2>&1; echo "WRITE_RC=$?"'
                     % (MARKER, STAMP, path))
        lines.append("ls -l %s 2>&1" % path)
        lines.append("cat %s 2>&1" % path)
        # 顺手确认挂载情况（/mnt/mmc 是不是 SD 卡、是否是同一分区）
        lines.append("df -h %s 2>&1 | tail -2" % path.rsplit("/", 1)[0])
    lines.append("echo '---------- mount 信息 ----------'")
    lines.append("mount | grep -E 'mmc|sdcard|tmpfs' 2>&1")
    lines.append("ls -ld /mnt/mmc /opt/storage/sdcard /tmp /var/log 2>&1")
    return lines


def main():
    print("=" * 74)
    print("NX500 诊断文件落盘位置排查   相机=%s" % CAMERA_IP)
    print("=" * 74)

    print("[1/2] telnet 依次往 4 个位置写测试文件 ...")
    outputs = {}
    try:
        for cmd, out in telnet_run(build_script()):
            outputs[cmd] = out
            if cmd.startswith("echo \""):
                # 写测试行：把 WRITE_RC 抠出来
                m = re.search(r"WRITE_RC=(\d+)", out)
                print("      %-46s -> rc=%s" % (cmd.split(">")[-1].strip(),
                                                m.group(1) if m else "?"))
    except Exception as exc:                      # noqa: BLE001
        print("      telnet 阶段失败：%r" % (exc,))

    print("[2/2] 逐个用 FTP 尝试取回 ...")
    print("-" * 74)
    print("%-24s %-34s %-8s %s" % ("写入位置", "文件", "FTP", "取回字节"))
    print("-" * 74)

    rows = []
    for label, path, rel in CANDIDATES:
        if rel is None:
            # 不在 FTP 根下，故意试一次"按绝对路径取"以证明取不回
            ok, size, note = ftp_fetch_size(path.lstrip("/"))
            rows.append((label, path, ok, size, note))
            print("%-24s %-34s %-8s %s" % (label, path, "失败" if not ok else "成功",
                                           ("%d 字节" % size) if ok else note[:40]))
        else:
            ok, size, note = ftp_fetch_size(rel)
            rows.append((label, path, ok, size, note))
            print("%-24s %-34s %-8s %s" % (label, rel, "成功" if ok else "失败",
                                           ("%d 字节" % size) if ok else note[:40]))

    print("-" * 74)
    print("命令行等价写法（给人复制用）：")
    for _label, _path, rel in CANDIDATES:
        if rel is None:
            continue
        print("  curl.exe --user root: -o %s ftp://%s/%s" % (rel, CAMERA_IP, rel))
    print("  注意 `root:` 的冒号不能省，那是空密码的表示法。")
    print("=" * 74)
    print("结论：**FTP 只提供 SD 卡根（/opt/storage/sdcard），"
          "/tmp 与 /var/log 取不回。**")
    print("      -> 诊断输出一律写 `/mnt/mmc/`（它就是 SD 卡挂载点），再 FTP 取回。")
    print("      -> 所有脚本里的 REMOTE_OUT 都遵守这条约定。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
