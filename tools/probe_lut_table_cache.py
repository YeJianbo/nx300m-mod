#!/usr/bin/env python3
"""Host probe for mod/metol/lut_table_cache.h.

Runs the real header against real files twice: once with a saved baseline copy
and once with the candidate. The read-time change is injected at the real
read() boundary by an LD_PRELOAD shim, so no thread races are involved.

Cases:
  plain    empty slot before a later hit, A->B->A loop reads, failure paths
  injected table rewritten in place while it is being read, then a retry

Exit code is nonzero if the candidate fails any check. The baseline is expected
to fail: its failures are the defect record, not a probe error.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "outputs" / "review" / "2026-11-15-cache-raw"
HEADER = ROOT / "mod" / "metol" / "lut_table_cache.h"
BASELINE = REVIEW / "baseline" / "lut_table_cache.h"
TEST = ROOT / "scratch" / "pi-table-cache-test.c"
INJECT = ROOT / "scratch" / "pi-table-cache-inject.c"


def run(cmd: str) -> tuple[int, str]:
    proc = subprocess.run(["wsl.exe", "-d", "Debian", "-u", "root", "-e", "sh", "-c", cmd],
                          capture_output=True, text=True, env=wsl_env())
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def wsl_path(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    return f"/mnt/d/codex/project/2026-10-19/nx300m-mod/{rel}"


def wsl_env() -> dict[str, str]:
    import os
    env = dict(os.environ)
    parts = [r"D:\Program Files\LLVM\bin", r"C:\Windows\System32"]
    env["PATH"] = ";".join(parts + [env.get("PATH", "")])
    return env


def parse_checks(text: str) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for line in text.splitlines():
        if line.startswith("CHECK "):
            payload = json.loads(line[len("CHECK "):])
            checks[payload["name"]] = bool(payload["ok"])
    return checks


def parse_info(text: str) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for line in text.splitlines():
        if line.startswith("INFO "):
            payload = json.loads(line[len("INFO "):])
            info[payload["case"]] = payload
    return info


def main() -> int:
    REVIEW.mkdir(parents=True, exist_ok=True)
    w = wsl_path(REVIEW)
    header_rel = HEADER.relative_to(ROOT).as_posix()
    baseline_rel = BASELINE.relative_to(ROOT).as_posix()
    test_rel = TEST.relative_to(ROOT).as_posix()
    inject_rel = INJECT.relative_to(ROOT).as_posix()
    flags = "-std=gnu99 -O1 -Wall -Wextra -Werror"

    steps: list[dict] = []
    build = (
        "cd /mnt/d/codex/project/2026-10-19/nx300m-mod && "
        f"cc {flags} -Imod/metol {test_rel} -o {w}/candidate/cache-test && "
        f"cc {flags} -I{baseline_rel.rsplit('/', 1)[0]} {test_rel} -o {w}/baseline/cache-test && "
        f"cc {flags} -shared -fPIC {inject_rel} -o {w}/candidate/inject.so -ldl"
    )
    rc, out = run(build)
    steps.append({"step": "build", "rc": rc, "ok": rc == 0, "output_tail": out.strip()[-800:]})
    if rc != 0:
        _write(steps, {})
        return 1

    runs: dict[str, tuple[int, str]] = {}
    for label, binary, mode, preload in (
        ("candidate_plain", "candidate", "plain", False),
        ("candidate_injected", "candidate", "injected", True),
        ("baseline_plain", "baseline", "plain", False),
        ("baseline_injected", "baseline", "injected", True),
    ):
        work = f"{w}/work/{binary}"
        pre = f"LD_PRELOAD={w}/candidate/inject.so " if preload else ""
        cmd = f"cd {w} && mkdir -p {work} && {pre}./{binary}/cache-test {mode} {work}"
        rc, out = run(cmd)
        runs[label] = (rc, out)
        steps.append({"step": label, "rc": rc, "ok": rc == 0, "output_tail": out.strip()[-600:]})

    cand_plain = parse_checks(runs["candidate_plain"][1])
    cand_inj = parse_checks(runs["candidate_injected"][1])
    base_plain = parse_checks(runs["baseline_plain"][1])
    base_inj = parse_checks(runs["baseline_injected"][1])
    cand_info = {**parse_info(runs["candidate_plain"][1]), **parse_info(runs["candidate_injected"][1])}
    base_info = {**parse_info(runs["baseline_plain"][1]), **parse_info(runs["baseline_injected"][1])}

    candidate_ok = all(cand_plain.values()) and all(cand_inj.values()) and \
        runs["candidate_plain"][0] == 0 and runs["candidate_injected"][0] == 0
    # These two are the defects the baseline must still exhibit.
    reproduced = {
        "empty_slot_reReads_the_file": base_plain.get("empty_slot_before_hit_no_reread") is False,
        "empty_slot_creates_duplicate": base_plain.get("no_duplicate_slot") is False,
        "read_change_accepted": base_inj.get("read_change_rejected") is False,
    }

    report = {
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "header": {
            "candidate": {"path": header_rel, "sha256": _sha(HEADER)},
            "baseline": {"path": baseline_rel, "sha256": _sha(BASELINE)},
        },
        "cases": {
            "candidate": {"plain": cand_plain, "injected": cand_inj},
            "baseline": {"plain": base_plain, "injected": base_inj},
        },
        "measurements": {"candidate": cand_info, "baseline": base_info},
        "candidate_all_checks_pass": candidate_ok,
        "baseline_reproduces_reported_defects": reproduced,
        "steps": steps,
        "injection": "LD_PRELOAD read() shim rewrites the table in place at the first read of the "
                     "target descriptor, between the open-time fstat and the post-read fstat",
        "not_covered": [
            "a writer that rewrites a published table inside one filesystem timestamp tick with unchanged size and inode",
            "concurrent readers on the camera rootfs (this fixture is single-threaded by design)",
            "device-side card latency",
        ],
    }
    _write(steps, report)
    print(json.dumps({"candidate_ok": candidate_ok, "baseline_reproduces": reproduced}, indent=2))
    return 0 if candidate_ok else 1


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write(steps: list[dict], report: dict) -> None:
    REVIEW.mkdir(parents=True, exist_ok=True)
    (REVIEW / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (REVIEW / "steps.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
