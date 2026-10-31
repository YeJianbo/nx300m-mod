"""Read or set the NX300M capture AE fastest-shutter upper bound."""

import argparse
import json
import secrets
import subprocess
import threading
import time

from session_stock import inspect_session

OPTIONS = ("off", "1/60", "1/125", "1/250", "1/500", "1/1000", "1/2000", "1/4000", "1/6000", "1/8000")
LOCK = threading.Lock()


def request(fastest=None):
    operation = int(fastest is not None)
    requested = OPTIONS.index(fastest) if fastest is not None else 0
    with LOCK:
        state = inspect_session()
        if not state["running"]:
            raise RuntimeError("请先启动已加载 shutter_limit.so 的原厂相机会话")
        if not state.get("shutter_limit_ready"):
            raise RuntimeError("当前原厂会话未加载快门上限桥或定时器尚未就绪")
        request_id = secrets.randbelow(0x7ffffffe) + 1
        command = [
            "wsl.exe", "-u", "root", "-e", "nsenter", "-t", str(state["namespace_pid"]), "--mount",
            "sh", "-c", '''set -eu
cd /opt/nx300m-lab/stock-1.15/var/run
exec 9> nx300m-shutter-limit-lock
flock -w 2 9
printf '%s\n' "$1" > nx300m-shutter-limit-request.tmp
mv nx300m-shutter-limit-request.tmp nx300m-shutter-limit-request
for attempt in $(seq 1 160); do
 if [ -f nx300m-shutter-limit-response ]; then
  read -r response_id rest < nx300m-shutter-limit-response
  if [ "$response_id" = "$2" ]; then cat nx300m-shutter-limit-response; exit 0; fi
 fi
 sleep 0.05
done
exit 124''',
            "sh", f"{request_id} {int(time.time()) + 6} {operation} {requested}", str(request_id),
        ]
        result = subprocess.run(command, capture_output=True, timeout=15)
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"快门上限桥未响应（退出码 {result.returncode}）：{detail}")
        fields = result.stdout.decode("ascii").split()
        if len(fields) != 10 or int(fields[0]) != request_id:
            raise RuntimeError("快门上限桥返回格式无效")
        values = [int(value) for value in fields]
        _, code, choice, tv16, microseconds, stock, actual, calls, capped, last_choice = values
        if code:
            raise RuntimeError(f"快门上限请求失败：{code}")
        if choice >= len(OPTIONS) or (operation and choice != requested):
            raise RuntimeError("快门上限读回与请求不一致")
        return {
            "namespace_pid": state["namespace_pid"], "fastest_target": OPTIONS[choice],
            "enabled": bool(choice), "limit_tv16": tv16 if choice else None,
            "limit_microseconds": microseconds if choice else None,
            "last_stock_tv16": stock if calls else None, "last_result_tv16": actual if calls else None,
            "getter_calls": calls, "capped_calls": capped,
            "configured_choice_observed_at_getter": bool(calls and last_choice == choice),
            "ae_solver_and_physical_exposure_verified": False, "persistent": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastest", choices=OPTIONS)
    args = parser.parse_args()
    print(json.dumps(request(args.fastest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
