"""Build the production bridge and check its new native CHM hooks once."""
import json
from pathlib import Path
import re
import shlex
import subprocess

from build_color_style import build_bridge
from build_ycc_probe import wsl_path

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/metol'


def main():
    source = json.loads((OUTPUT / 'nx500-pw-source.json').read_text(encoding='utf-8'))
    expected = [entry['chroma_2x2_q8'] for entry in source['source_color_operators']['hue']['entries']]
    header = (PROJECT / 'runtime/nx500_color_operator.h').read_text(encoding='utf-8')
    body = re.search(r'chroma_hue_q8\[21\]\[4\]\s*=\s*\{(.*?)\n\};', header, re.S)
    actual = [[int(value) for value in row.split(',')]
              for row in re.findall(r'\{([^{}]+)\}', body.group(1))] if body else []
    if actual != expected:
        raise SystemExit('Production HUE coefficients do not match the actual NX500 source tables')
    build_bridge()
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    base, rootfs, sdk = (wsl_path(value) for value in
                          (PROJECT, config['rootfs'], PROJECT / config['sdk']))
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
base={shlex.quote(base)}
sdk={shlex.quote(sdk)}
cd "$base"
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -shared -fPIC -nostdlib \\
 runtime/color_style_probe_ecore.c -o outputs/metol/chroma-style-probe-ecore.so
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined -Wl,--export-dynamic \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -I"$sdk/usr/include" \\
 runtime/chroma_style_probe.c outputs/metol/nx500-color-original.S work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -Wl,--no-as-needed outputs/metol/chroma-style-probe-ecore.so \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o outputs/metol/chroma-style-probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 exec "$1/qemu-arm-static" -L "$1" \\
  -E LD_LIBRARY_PATH="$2:$1/usr/lib:$1/lib" \\
  -E LD_PRELOAD="$2/outputs/metol/color-style.so" \\
  "$2/outputs/metol/chroma-style-probe.arm"
' sh "$rf" "$base"
'''
    result = subprocess.run(['wsl.exe','-u','root','-e','sh','-c',script],
                            capture_output=True, timeout=60)
    stdout, stderr = (data.decode('utf-8', 'replace') for data in (result.stdout,result.stderr))
    (OUTPUT / 'chroma-style-probe.stdout.log').write_text(stdout, encoding='utf-8')
    (OUTPUT / 'chroma-style-probe.stderr.log').write_text(stderr, encoding='utf-8')
    passed = result.returncode == 0 and 'CHROMA_BRIDGE_PROBE=PASS;' in stdout
    report = {
        'exit_code': result.returncode, 'passed': passed,
        'bridge': 'outputs/metol/color-style.so',
        'source_hue_table_matches': True, 'source_hue_rows_checked': len(expected),
        'supported_hue': True, 'supported_saturation': 'standard/custom1 P/A/S/M with unit stock CHM',
        'backend': 'original libudd setters, synthetic RAM, isolated Ecore fixture; shared camera/VM untouched',
        'stdout': stdout.strip().splitlines(),
        'checks': ['neutral passthrough','cached HUE application','fresh base','repeat does not accumulate',
                   'source rounding including negative coefficients','source table 235/234 asymmetry',
                   'reset latest base','whole matrix signed11 fallback','other selectors forward unchanged outside the complete path',
                   'incomplete base rejection','out-of-range index rejection','trailing token rejection',
                   'complete SAT then HUE matches original ARM leaves and setters',
                   'fresh YCC recomposition without accumulation','mode change restores both bases','complete reset','Custom1 source operator','neutral SAT retains complete source order',
                   'real original setter failure is preserved in response'],
        'camera_started': False,'stock_files_modified': False,'image_verified': False,
    }
    (OUTPUT / 'chroma-style-probe.json').write_text(json.dumps(report,indent=2) + '\n',encoding='utf-8')
    print(json.dumps({'exit_code':result.returncode,'passed':passed,
                      'source_hue_rows_checked':len(expected),'report':str(OUTPUT / 'chroma-style-probe.json')},indent=2))
    if not passed:
        raise SystemExit(f'Production CHM probe failed: exit={result.returncode}; stderr={stderr.strip()}; stdout={stdout.strip()}')


if __name__ == '__main__':
    main()
