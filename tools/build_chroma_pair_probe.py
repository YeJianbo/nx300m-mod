"""Build and exercise paired selector recovery against original ARM setters."""
import json
from pathlib import Path
import shlex
import subprocess
from build_ycc_probe import wsl_path

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/metol'


def main():
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs, base, sdk = map(wsl_path, (config['rootfs'], PROJECT, PROJECT / config['sdk']))
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(sdk+'/usr/include')} \\
 runtime/chroma_pair_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -Wl,--no-as-needed "$rf/usr/lib/libudd.so.3.1" \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o outputs/metol/chroma-pair-probe.arm
unset LD_LIBRARY_PATH
"$rf/qemu-arm-static" -L "$rf" {shlex.quote(base+'/outputs/metol/chroma-pair-probe.arm')}
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
                            capture_output=True, timeout=60)
    stdout, stderr = (data.decode('utf-8', 'replace') for data in (result.stdout, result.stderr))
    (OUTPUT / 'chroma-pair-probe.stdout.log').write_text(stdout, encoding='utf-8')
    (OUTPUT / 'chroma-pair-probe.stderr.log').write_text(stderr, encoding='utf-8')
    cases = [dict(field.split('=', 1) for field in line.split()[1:])
             for line in stdout.splitlines() if line.startswith('PAIR_CASE ')]
    passed = result.returncode == 0 and len(cases) == 16 and all(c['pass'] == '1' for c in cases)
    report = dict(exit_code=result.returncode, passed=passed, cases=cases,
                  backend='Original IPCS/IPCM setters with separately allocated register RAM',
                  failures='Return 7 injected at the wrapper for second-write and rollback failure cases',
                  deployed_to_camera=False, camera_started=False, image_verified=False,
                  scope='Pair application and recovery; no source mode selection or frame-atomicity guarantee')
    (OUTPUT / 'chroma-pair-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not passed:
        raise RuntimeError(f'Pair probe exit {result.returncode}: {stderr.strip()}')


if __name__ == '__main__':
    main()
