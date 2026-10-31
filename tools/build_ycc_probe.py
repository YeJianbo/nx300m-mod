"""Build then run the stock YCC setters once against synthetic register RAM."""
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/metol'


def wsl_path(path):
    value = str(path).replace('\\', '/')
    prefix = '//wsl.localhost/Debian/'
    if value.startswith(prefix):
        return '/' + value[len(prefix):]
    if len(value) > 2 and value[1] == ':':
        return '/mnt/' + value[0].lower() + value[2:]
    if value.startswith('/'):
        return value
    raise ValueError(f'Cannot map to WSL: {path}')


def main():
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rootfs = wsl_path(config['rootfs'])
    base = wsl_path(PROJECT)
    sdk = wsl_path(PROJECT / config['sdk'])
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
base={shlex.quote(base)}
sdk={shlex.quote(sdk)}
cd "$base"
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -I"$sdk/usr/include" \\
 runtime/ycc_abi_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -Wl,--no-as-needed "$rf/usr/lib/libudd.so.3.1" \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o outputs/metol/ycc-abi-probe.arm
unset LD_LIBRARY_PATH
"$rf/qemu-arm-static" -L "$rf" "$base/outputs/metol/ycc-abi-probe.arm"
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
                            capture_output=True, timeout=60)
    stdout, stderr = (data.decode('utf-8', 'replace') for data in (result.stdout, result.stderr))
    (OUTPUT / 'ycc-abi-probe.stdout.log').write_text(stdout, encoding='utf-8')
    (OUTPUT / 'ycc-abi-probe.stderr.log').write_text(stderr, encoding='utf-8')
    cases = [dict(field.split('=', 1) for field in line.split()[1:])
             for line in stdout.splitlines() if line.startswith('YCC_CASE ')]
    passed = (result.returncode == 0 and 'YCC_ABI_PROBE=PASS;' in stdout
              and len(cases) == 10 and all(case['pass'] == '1' for case in cases))
    report = {
        'exit_code': result.returncode, 'passed': passed, 'case_count': len(cases),
        'library': rootfs + '/usr/lib/libudd.so.3.1',
        'sdk_headers': config['sdk'] + '/usr/include/drime4/udd/{ipcs,ipcm}_submodule_type.h',
        'backend': 'qemu-arm user-mode process; genuine setters; separate allocated RAM for each register base',
        'cases': cases, 'stock_files_modified': False, 'camera_started': False,
        'shared_vm_changed': False, 'image_verified': False,
        'scope': 'ABI validation, signed range rejection and full RAM packing comparison only; does not evaluate pixels or rotation direction.',
    }
    (OUTPUT / 'ycc-abi-probe.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(f'YCC probe failed: exit={result.returncode}; stderr={stderr.strip()}; stdout={stdout.strip()}')


if __name__ == '__main__':
    main()
