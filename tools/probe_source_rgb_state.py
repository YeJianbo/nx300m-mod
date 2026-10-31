"""Run eight bounded production-header control-state cases with explicit mocks."""
import json
from pathlib import Path
import shlex
import subprocess

from build_ycc_probe import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/metol'


def main():
    base = wsl_path(ROOT)
    build_script = f'''set -eu
cd {shlex.quote(base)}
gcc -std=c99 -O2 -Wall -Wextra -Werror -Wno-unused-function -pthread \
 runtime/source_rgb_state_probe.c -o outputs/metol/source-rgb-state-probe.host
'''
    build = subprocess.run(['wsl.exe','-e','sh','-c',build_script], capture_output=True, timeout=30)
    (OUT/'source-rgb-state-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'State fixture build exit{build.returncode}: {build.stderr.decode("utf-8","replace")}')
    run = subprocess.run(['wsl.exe','-e',base+'/outputs/metol/source-rgb-state-probe.host'],
                         capture_output=True, timeout=15)
    stdout, stderr = (value.decode('utf-8','replace') for value in (run.stdout,run.stderr))
    (OUT/'source-rgb-state-probe.stdout.log').write_text(stdout,encoding='utf-8')
    (OUT/'source-rgb-state-probe.stderr.log').write_text(stderr,encoding='utf-8')
    cases = [dict(field.split('=',1) for field in line.split()[1:])
             for line in stdout.splitlines() if line.startswith('STATE_CASE ')]
    passed = run.returncode == 0 and len(cases) == 8 and all(item['failures']=='0' for item in cases)
    report = {'build_exit_code':build.returncode,'exit_code':run.returncode,'passed':passed,'cases':cases,
              'production_header':'runtime/source_rgb_style.h',
              'fixture':'runtime/source_rgb_state_probe.c',
              'scope':'Host-native control-state regression only; includes unchanged production header. Explicit getter, Update, SDK return and file-I/O fixtures. Tests actual14-field response derivation, TLS nesting, a deterministic two-thread write/completion interleaving, idle preservation, failed clears, mode restoration and cache owner replay.',
              'not_verified':'No original firmware consumer, ARM arithmetic, ISP register or pixel execution; original-source math was not rerun.',
              'production_files_modified':False,'running_session_changed':False,'stdout':stdout,'stderr':stderr}
    (OUT/'source-rgb-state-probe.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not passed:
        raise SystemExit(run.returncode or 1)


if __name__ == '__main__':
    main()
