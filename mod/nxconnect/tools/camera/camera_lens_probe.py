#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
camera_lens_probe.py —— NX500 镜头信息外部可读性探测

背景
----
相机自带一个叫 `st` 的命令行工具（Samsung 内部的调试/控制工具）。当初想通过
`st cap lens <sub>` 把镜头信息（焦距、光圈范围、变焦位置、对焦位置等）读出来，
好让 App 显示更完整的镜头状态。

做法
----
批量试 `st cap lens` 的各个子命令，把**所有输出重定向到相机端的文件**里
（/mnt/mmc/lens.txt），最后用 FTP 一次性取回本地阅读。
这样做而不是逐条读 stdout 的原因：telnet 逐条读容易丢行、也容易卡在提示符上，
一次性重定向 + 分节标记最省事。

实测结论（重要，别再重复试了）
------------------------------
* 这些子命令**全部**返回：
      Cann't execute capture command over MCB!
  （原文如此，`Cann't` 是相机端固件的拼写错误）
* 原因：相机 App 自己占着 MCB（Micro Controller Bus）总线，外部进程拿不到
  镜头/捕获子系统的控制权。这是**权限/占用**问题，不是命令名写错。
* 所以：**镜头信息无法从外部读取**，App 端只能显示 daemon 给的有限字段，
  不要为此再写探测脚本了。

踩过的坑
--------
* 相机上**没有 `timeout` 命令**（busybox 未编入该 applet）。
  历史上这里写过 `timeout 5 st cap lens info`，结果：
      -sh: timeout: command not found
  所以本脚本**不使用 timeout**，每条命令直接跑（`st` 自己会很快返回）。
"""

import ftplib
import io
import re
import sys

# ---------------------------------------------------------------------------
# 真机参数（全部为实测值）
# ---------------------------------------------------------------------------
CAMERA_IP = "192.168.5.39"

TELNET_PORT = 23          # 用户 root，密码为空
FTP_PORT = 21             # 用户 root，密码为空，根目录 = SD 卡 /opt/storage/sdcard

# FTP 根 = SD 卡挂载点；/mnt/mmc 与它是同一个分区（/mnt/mmc -> /opt/storage/sdcard）
REMOTE_OUT = "/mnt/mmc/lens.txt"        # shell 侧写文件用绝对路径
FTP_OUT = "lens.txt"                    # FTP 侧用相对路径（根即 SD 卡）

TELNET_USER = "root"
TELNET_PASS = ""          # 实测：空密码
FTP_USER = "root"
FTP_PASS = ""

# 每个分节在本地打印多少行，避免刷屏
PREVIEW_LINES = 8

# 要试的子命令（全部实测失败，保留列表作为"以后想再挖"的入口）
LENS_SUBCOMMANDS = [
    "info",
    "general",
    "getrange",
    "status",
    "pdget",
    "sdget",
    "lensproperty",
    "lensinfo",
    "zoom",
    "focus_pos",
]

# 提示符匹配：必须是**行尾**正则，且带 re.M。
# 关键点：后续提示符是 `[root@drime5 ~]#`，**没有尾随空格**（被内核吞了），
# 只有首个提示符带空格。所以绝不能用 `"# " in buf` 这种子串判断。
PROMPT_RE = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    """剥掉 ANSI 颜色码；剥提示符之前必须先做这一步。"""
    return ANSI_RE.sub("", s)


def looks_like_prompt(text: str) -> bool:
    """判断缓冲区尾部是否是 shell 提示符（行尾匹配，容忍无尾随空格）。"""
    return PROMPT_RE.search(strip_ansi(text)) is not None


# ---------------------------------------------------------------------------
# 远程执行：走 telnet
# ---------------------------------------------------------------------------
def telnet_run(commands, host=CAMERA_IP, port=TELNET_PORT, timeout=10.0):
    """
    登录 telnet 并依次执行命令，返回 (命令, 输出) 列表。

    这里把「telnet 客户端的两个必踩坑」都处理掉了：
    1. 必须过滤 IAC 协商字节。
    2. 提示符必须用行尾正则匹配，不能用子串 `"# " in buf`。
    """
    import socket

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = ""
    results = []

    def pump(until_prompt=True, quiet=False):
        """按行缓冲地读 socket；socket.timeout 不能清空已读缓冲。"""
        nonlocal buf
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                # 超时也要保留 buf 里已经读到的内容（这是踩过的坑）
                break
            if not chunk:
                break
            buf += decode_telnet(chunk)
            if not quiet and until_prompt and looks_like_prompt(buf):
                break
        return buf

    def send(line):
        sock.sendall((line + "\n").encode("latin-1", "ignore"))

    pump(quiet=True)                     # 吃掉 banner
    send("")                             # 触发首个提示符
    pump(quiet=True)

    for cmd in commands:
        buf = ""                         # 每条命令单独收集输出
        send(cmd)
        pump()
        text = strip_ansi(buf)
        # 去掉回显的命令行本身与提示符尾巴
        lines = [ln for ln in text.splitlines() if ln.strip() != cmd.strip()]
        results.append((cmd, "\n".join(lines).strip()))

    try:
        sock.close()
    except OSError:
        pass
    return results


def decode_telnet(raw: bytes) -> str:
    """
    解码 telnet 数据流，过滤 IAC 协商。

    规则（实测有效）：
      遇到 255(IAC) 读下一字节当作命令；
      若命令在 0xFB..0xFE（WILL/WONT/DO/DONT）则吞 **3** 字节，
      否则吞 **2** 字节。
    服务器一连上就发：
      \\xff\\xfd\\x01 \\xff\\xfd\\x1f \\xff\\xfb\\x01 \\xff\\xfb\\x03
    并且**开启回显**（所以输出里会带命令回显，需要自己剔除）。
    """
    IAC = 255
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            break                                   # 不完整，丢弃
        cmd = raw[i + 1]
        if 0xFB <= cmd <= 0xFE:
            i += 3                                  # IAC + 命令 + 选项：吞 3
        else:
            i += 2                                  # 其余（如 IAC IAC）：吞 2
        # 若是 WILL/WONT/DO/DONT，这里本应回一个应答；
        # 实测不回也能正常拿到 shell，故简化省略。
    # 落到 latin-1 再尽量转 utf-8，避免解码异常中断流程
    return out.decode("latin-1", "replace")


# ---------------------------------------------------------------------------
# FTP 取回
# ---------------------------------------------------------------------------
def ftp_fetch(remote_name, host=CAMERA_IP, port=FTP_PORT,
              user=FTP_USER, password=FTP_PASS):
    """从 FTP 取回文件内容（bytes）。FTP 根 = SD 卡 /opt/storage/sdcard。"""
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


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build_script():
    """
    生成相机端要执行的 shell 片段。

    每条命令前用 `echo "########## <命令> ##########"` 做分节标记，
    便于回取后阅读（这是本脚本的核心输出格式约定）。
    注意：不要给命令加 timeout —— 相机上没有这个命令。
    """
    lines = [
        "echo '=== camera_lens_probe: st cap lens subcommands ==='",
        "date",                                     # 记录探测时刻（注意相机时钟可能停在 2015-02-17）
        "which st",
        "st",
        "> %s" % REMOTE_OUT,                        # 清空输出文件（相机端 /bin/sh 支持）
    ]
    for sub in LENS_SUBCOMMANDS:
        cmd = "st cap lens %s" % sub
        lines.append('echo "########## %s ##########" >> %s' % (cmd, REMOTE_OUT))
        # stderr 也一起收，否则错误信息（如 MCB 报错如果走 stderr）会丢
        lines.append("%s >> %s 2>&1" % (cmd, REMOTE_OUT))
    lines.append('echo "########## END ##########" >> %s' % REMOTE_OUT)
    lines.append("wc -l %s" % REMOTE_OUT)
    return lines


def parse_sections(text):
    """把 lens.txt 按 `########## ... ##########` 切成 [(标题, 正文)]。"""
    sections = []
    cur_title, cur_body = None, []
    for line in text.splitlines():
        m = re.match(r"^#{10}\s*(.*?)\s*#{10}$", line.strip())
        if m:
            if cur_title is not None:
                sections.append((cur_title, "\n".join(cur_body).strip()))
            cur_title, cur_body = m.group(1), []
        else:
            if cur_title is not None:
                cur_body.append(line)
    if cur_title is not None:
        sections.append((cur_title, "\n".join(cur_body).strip()))
    return sections


def main():
    print("[1/3] 通过 telnet 在相机上批量执行 st cap lens 子命令 ...")
    print("      相机 IP=%s port=%d （用户 root / 空密码）" % (CAMERA_IP, TELNET_PORT))
    print("      注意：相机上没有 timeout 命令，所以不做超时包装。")

    try:
        telnet_run(build_script())
    except Exception as exc:                      # noqa: BLE001
        print("      telnet 阶段失败：%r" % (exc,))
        print("      仍然尝试直接通过 FTP 取回上一次留下的 %s" % FTP_OUT)

    print("[2/3] 通过 FTP 取回 %s -> 本地 ./%s ..." % (REMOTE_OUT, FTP_OUT))
    try:
        data = ftp_fetch(FTP_OUT)
    except Exception as exc:                      # noqa: BLE001
        print("      FTP 取回失败：%r" % (exc,))
        return 1

    text = data.decode("latin-1", "replace")
    with open(FTP_OUT, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(text)
    print("      取回 %d 字节，已存本地 %s" % (len(data), FTP_OUT))

    print("[3/3] 分节预览（每节只打印前 %d 行，避免刷屏）" % PREVIEW_LINES)
    sections = parse_sections(text)
    if not sections:
        print("      （没有解析到分节标记，原始内容前 20 行如下）")
        for line in text.splitlines()[:20]:
            print("      | " + line)
        return 0

    for title, body in sections:
        print("-" * 68)
        print("## " + title)
        body_lines = body.splitlines() if body else []
        if not body_lines:
            print("   (无输出)")
        for line in body_lines[:PREVIEW_LINES]:
            print("   " + line)
        if len(body_lines) > PREVIEW_LINES:
            print("   ... （省略 %d 行，完整内容见 %s）"
                  % (len(body_lines) - PREVIEW_LINES, FTP_OUT))

    print("=" * 68)
    print("结论：以上 st cap lens 的所有子命令实测都返回")
    print("      Cann't execute capture command over MCB!")
    print("      相机 App 占用 MCB 总线，镜头信息无法从外部读取。")
    print("      这是既定事实，不要重复尝试；App 侧只需展示 daemon 提供的字段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
