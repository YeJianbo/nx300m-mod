"""Rediscover a specified phone and send only generated text using ARM/QEMU.

Run with the project Conda Python. The receiver's actual file/open result must
be entered separately; process success alone never sets transfer_success=true.
"""
import argparse
import datetime
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def wsl(path):
    return "/mnt/" + str(path)[0].lower() + "/" + str(path)[3:].replace("\\", "/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-ip", required=True, help="User-confirmed receiver IP")
    parser.add_argument("--name", required=True)
    parser.add_argument("--label", default="EOF")
    args = parser.parse_args()
    if not args.label.isalnum():
        parser.error("label must be alphanumeric")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = ROOT / "outputs/quickshare" / ("phone-test-" + stamp)
    run.mkdir()
    discovery = subprocess.run(
        [str(ROOT / "outputs/quickshare/nx-quickshare.exe"), "discover", "8"],
        capture_output=True, timeout=20,
    )
    (run / "windows-discover.jsonl").write_bytes(discovery.stdout)
    (run / "windows-discover.stderr.log").write_bytes(discovery.stderr)
    endpoints = [json.loads(line).get("endpoint", {}) for line in discovery.stdout.splitlines()]
    endpoint = next((e for e in endpoints if e.get("ip") == args.target_ip and e.get("present")), None)
    if not endpoint:
        raise SystemExit("Phone not rediscovered; no transfer attempted: " + str(run))
    path = run / ("NX300M-QuickShare-" + args.label + "-test.txt")
    path.write_text("NX300M Quick Share ARM generated test.\nRun: " + run.name + "\n", encoding="utf-8")
    addr = endpoint["ip"] + ":" + str(endpoint["port"])
    artifact = ROOT / "outputs/quickshare/nx-quickshare-armv7"
    report = {
        "target": args.name, "endpoint": addr, "backend": "ARMv7 via independent QEMU using WSL host network",
        "artifact_mtime_ns": artifact.stat().st_mtime_ns,
        "file": path.name, "bytes": path.stat().st_size, "exit_code": None,
        "receiver_content_verified": False, "receiver_result": "pending_user_confirmation",
        "transfer_success": None,
    }
    report_path = run / "arm-send-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run": str(run), "endpoint": addr, "file": path.name}), flush=True)
    with (run / "arm-send.jsonl").open("wb") as out, (run / "arm-send.stderr.log").open("wb") as err:
        result = subprocess.run([
            "wsl.exe", "-u", "root", "-e", "env", "NX_QS_DEVICE_NAME=NX300M ARM Test", "RUST_LOG=info",
            "/opt/nx300m-lab/stock-1.15/qemu-arm-static", "-cpu", "cortex-a9", wsl(artifact),
            "send", addr, args.name, wsl(path),
        ], stdout=out, stderr=err, timeout=195)
    events = [json.loads(line) for line in (run / "arm-send.jsonl").read_text().splitlines()]
    report["exit_code"] = result.returncode
    report["states"] = [e.get("message", {}).get("state") for e in events]
    report["protocol_confirmed"] = result.returncode == 0 and "Finished" in report["states"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    print((run / "arm-send.stderr.log").read_text()[-5500:], flush=True)


if __name__ == "__main__":
    main()
