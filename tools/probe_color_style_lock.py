"""Exercise the real preload with gated stock-setter fixtures on host threads."""
from wsl_paths import wsl_path
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/runtime-locks'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    base = wsl_path(ROOT)
    script = f'''set -eu
cd {shlex.quote(base)}
out=outputs/review/runtime-locks
cc -std=c99 -O2 -Wall -Wextra -Werror -shared -fPIC -pthread runtime/color_style.c -ldl -o "$out/color-style-host.so"
cc -DFIXTURE_LIBRARY -std=c99 -O2 -Wall -Wextra -Werror -shared -fPIC -pthread runtime/color_style_lock_probe.c -o "$out/lock-fixture.so"
cc -std=c99 -O2 -Wall -Wextra -Werror -pthread runtime/color_style_lock_probe.c -L"$out" -Wl,--no-as-needed -l:lock-fixture.so -o "$out/lock-probe"
exec unshare --mount --net --ipc sh -ec '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 exec env LD_LIBRARY_PATH="$1/outputs/review/runtime-locks" LD_PRELOAD="$1/outputs/review/runtime-locks/color-style-host.so" "$1/outputs/review/runtime-locks/lock-probe"
' sh {shlex.quote(base)}
'''
    run = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
                         capture_output=True, timeout=40)
    stdout, stderr = (data.decode('utf-8', 'replace') for data in (run.stdout, run.stderr))
    (OUT / 'color-lock.stdout.log').write_text(stdout, encoding='utf-8')
    (OUT / 'color-lock.stderr.log').write_text(stderr, encoding='utf-8')
    report = {'exit_code': run.returncode,
              'passed': run.returncode == 0 and 'COLOR_LOCK_PROBE=PASS' in stdout,
              'scope': 'Host pthread scheduling and production preload; SDK setters are gated fixtures, not camera timing.',
              'stdout': stdout, 'stderr': stderr}
    (OUT / 'color-lock.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(run.returncode or 1)


if __name__ == '__main__':
    main()
