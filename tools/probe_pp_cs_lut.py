"""Build an isolated CS MDMA device/probe and run unmodified libudd load APIs.

Only the private probe module is copied to its own rootfs mod directory.
No camera launch, production shim overwrite, or active namespace operation.
"""
import json
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime/pp-cs-lut'
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
                    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
                    '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
                    '-Wl,-z,nocombreloc', 'runtime/device_preview.c', '-o', str(OUT/'device.so')], cwd=ROOT, check=True)
    base = '/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/pp_cs_lut_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/pp-cs-lut/probe.arm
mkdir -p "$rf/mod/pp-cs-lut-probe"
cp outputs/firmware-runtime/pp-cs-lut/probe.arm "$rf/mod/pp-cs-lut-probe/probe.arm"
cp outputs/firmware-runtime/pp-cs-lut/device.so "$rf/mod/pp-cs-lut-probe/device.so"
cp outputs/firmware-runtime/ep-cs-grids-nx300m-cs-upload-lut1-42720.bin "$rf/mod/pp-cs-lut-probe/source.bin"
chmod 755 "$rf/mod/pp-cs-lut-probe/probe.arm"
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount --make-rprivate /
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_ISP_SIM=1 -E LD_PRELOAD=/mod/pp-cs-lut-probe/device.so -cpu cortex-a9 -L / /mod/pp-cs-lut-probe/probe.arm'
'''
    process = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', script],
                             capture_output=True, timeout=45)
    report = {'exit_code': process.returncode,
              'stock_calls': ['pp_core_start_lut_update', 'pp_core_wait_lut_update',
                              'pp_core_common_lut_update', 'pp_core_common_lut_init'],
              'source': 'ep-cs-grids-nx300m-cs-upload-lut1-42720.bin and varied reuse bytes',
              'scope': 'single-source CS MDMA byte transfer and LUT completion lifecycle',
              'pixel_processing_implemented': False, 'new_capture_jpeg_verified': False,
              'stdout': process.stdout.decode('utf-8', 'replace'),
              'stderr': process.stderr.decode('utf-8', 'replace')}
    for name, data in [('stdout', process.stdout), ('stderr', process.stderr)]:
        (OUT/f'probe.{name}.log').write_bytes(data)
    (OUT/'probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(process.returncode))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
