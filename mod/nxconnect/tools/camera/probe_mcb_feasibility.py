"""H 组可行性：能否绕开相机 UI 直接读/设曝光参数（MCB 路径）。

背景：
  - daemon 的 HTTP API **没有**任何参数接口，只有按键/鼠标注入与取景。
  - 相机自带 `st` 调试命令，理论上能直连 MCB：
      st cap capdtm getusr <参数>      读用户参数
      st cap capdtm setusr <参数> <值> 设用户参数（如果支持）
      st cap capt mcb <cat> <id> <len> <data>   原始 MCB 命令
  - 但 `st cap lens` 实测被 MCB 占用挡住：
      "Cann't execute capture command over MCB!"
    所以关键问题是：**capdtm 是否同样被挡**。

本脚本用一个「统一输出重定向到文件再取回」的手法，彻底避开 telnet 回显干扰
（回显问题已经坑了好几轮）。所有命令的 stdout/stderr 都写进 /mnt/mmc/h.txt，
最后 cat 一次；同时用 HTTP 侧对照相机当前模式是否变化。
"""
import re
import socket
import subprocess
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.5.39"
IAC = 255
PROMPT = re.compile(r"(\[[^\]]*\]\s*)?#\s*$", re.M)


class Shell:
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 23), timeout=6)
        self.s.settimeout(0.6)
        self.buf = ""

    def _pump(self):
        try:
            d = self.s.recv(8192)
        except socket.timeout:
            return True
        except Exception:
            return False
        if not d:
            return False
        i, n = 0, len(d)
        while i < n:
            b = d[i]
            if b == IAC:
                if i + 1 >= n:
                    break
                i += 3 if 0xFB <= d[i + 1] <= 0xFE else 2
                continue
            self.buf += chr(b)
            i += 1
        return True

    def await_text(self, t, ms):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if t in self.buf:
                return True
            if not self._pump():
                return False
        return t in self.buf

    def login(self):
        if not self.await_text("login:", 8000):
            raise RuntimeError("没看到 login")
        self.buf = ""
        self.s.sendall(b"root\n")
        if not self.await_text("#", 8000):
            raise RuntimeError("没进 shell")
        self.buf = ""

    def run_quiet(self, cmd, ms=20000):
        """
        把命令输出重定向到文件，只回显一个 'DONE'。
        这样 telnet 回显里不会有命令输出，也就不会有换行把输出打乱的问题。
        """
        self.buf = ""
        full = "%s >/mnt/mmc/h.txt 2>&1; echo DONE_%d" % (cmd, int(time.time() * 1000) % 100000)
        self.s.sendall((full + "\n").encode())
        end = time.time() + ms / 1000.0
        while time.time() < end:
            if "DONE_" in self.buf:
                break
            if not self._pump():
                break
        # 等落盘
        time.sleep(0.4)

    def read_file(self, path="/mnt/mmc/h.txt"):
        r = subprocess.run(["curl.exe", "-s", "--user", "root:",
                            "ftp://%s%s" % (IP, path)],
                           capture_output=True, text=True, errors="replace")
        return r.stdout or ""

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


PROBES = [
    ("st cap capdtm getusr ADJUSTSHUTTERTYPE",
     "读快门类型（已知可用，作为对照）"),
    ("st cap capdtm getusr",
     "列出所有用户参数（无参数调用）"),
    ("st cap capdtm --help; st cap capdtm",
     "看 capdtm 的用法，确认有没有 set") ,
    ("st cap capt lens info",
     "已知会被 MCB 挡住 —— 作为「占用」的对照"),
    ("cat /proc/$(pidof nx-remote-controller-daemon)/status 2>/dev/null | head -4; echo ---; pidof nx-remote-controller-daemon",
     "看 daemon 的 PID（确认它是否在跑）"),
    ("st cap capt single start",
     "用 st 直接触发单张拍摄（若可行，等于绕过按键注入）"),
    ("cat /etc/version.info",
     "固件版本对照"),
]


def main():
    sh = Shell(IP)
    sh.login()
    print("=== 相机 shell 已连上，逐条探测（输出走文件，避免回显干扰）===\n")

    for cmd, note in PROBES:
        print("=" * 70)
        print("【%s】" % note)
        print("  $ %s" % cmd)
        sh.run_quiet(cmd, ms=25000)
        out = sh.read_file()
        for line in out.splitlines()[:25]:
            print("    %s" % line)
        if not out.strip():
            print("    (无输出)")
        print()

    print("=" * 70)
    print("=== 额外：st cap 的子命令帮助（找 set/参数相关）===")
    sh.run_quiet("st cap capt 2>&1 | grep -i 'mcb\\|timer\\|bulb' ", ms=20000)
    print(sh.read_file()[:1200])

    print("\n=== 额外：capdtm 的所有字符串（看有没有 setusr）===")
    sh.run_quiet("strings /usr/bin/st 2>/dev/null | grep -i capdtm | head -30", ms=20000)
    print(sh.read_file()[:1200])

    sh.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
