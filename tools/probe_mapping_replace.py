"""Build and run a private ARM mapping probe, without touching the camera shim."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/firmware-runtime/mapping-replace'
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
                    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
                    '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
                    '-Wl,-z,nocombreloc', 'runtime/device_preview.c', '-o', str(OUT / 'device.so')],
                   cwd=ROOT, check=True)
    base = '/mnt/' + ROOT.drive[0].lower() + ROOT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/mapping_replace_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libdl.so.2" \\
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/mapping-replace/probe.arm
mkdir -p "$rf/mod/mapping-replace-probe"
cp outputs/firmware-runtime/mapping-replace/probe.arm "$rf/mod/mapping-replace-probe/probe.arm"
cp outputs/firmware-runtime/mapping-replace/device.so "$rf/mod/mapping-replace-probe/device.so"
chmod 755 "$rf/mod/mapping-replace-probe/probe.arm"
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -ec 'mount --make-rprivate /
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_PWM_TIMING=1 -E LD_PRELOAD=/mod/mapping-replace-probe/device.so -cpu cortex-a9 -L / /mod/mapping-replace-probe/probe.arm
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_MAPPING_RESERVED_FAILURE=1 -E NX300M_PWM_TIMING=1 -E LD_PRELOAD=/mod/mapping-replace-probe/device.so -cpu cortex-a9 -L / /mod/mapping-replace-probe/probe.arm' 
'''
    process = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', script],
                             capture_output=True, timeout=45)
    report = {'exit_code': process.returncode,
              'scope': 'tracked mmap/MAP_FIXED, munmap, mprotect and Linux-3.5 mremap intervals/access; partial EACCES and stale QEMU guest maps; PWM stock pthread startup; ARM glibc 2.13',
              'mprotect_partial_failure_verified': process.returncode == 0 and
              b'MPROTECT partial_eacces=pass guest_maps_stale_prot=1 actual_prefix_write_status=0 unknown_denied=pass restore_identity=pass actual_readonly_restored=pass' in process.stdout,
              'pwm_stock_thread_start_verified': process.returncode == 0 and
              b'PWM_INIT stock_thread_start_under_mapping_shim=pass' in process.stdout,
              'live_allocation_snapshot_verified': process.returncode == 0 and
              b'LIVE_SNAPSHOT exact_bytes=pass chunks=2 overlap_denied=pass invalid_destination=pass read_permission=pass freed_allocation_denied=pass' in process.stdout,
              'live_generation_submit_verified': process.returncode == 0 and
              b'LIVE_SUBMIT exact_bytes=pass chunks=2 invalid_source=pass readonly_denied=pass freed_allocation_denied=pass reused_generation_denied=pass private_alias_denied=pass' in process.stdout,
              'mremap_default_reserved_va_verified': process.returncode == 0 and
              b'MREMAP default_reserved_va_move=pass maymove_failure_errno=14 actual_source_cleared=pass failed_source_revoked=pass' in process.stdout,
              'mremap_semantics_sources': [
                  {'url': 'https://raw.githubusercontent.com/torvalds/linux/v3.5/mm/mremap.c',
                   'functions': ['mremap_to', 'move_vma', 'SYSCALL_DEFINE5(mremap)'],
                   'finding': 'FIXED unmaps destination and a shrinking old tail before source validation; move_vma rolls moved page tables back on failure, not those earlier unmaps'},
                  {'url': 'https://raw.githubusercontent.com/qemu/qemu/v11.0.0/linux-user/mmap.c',
                   'function': 'target_mremap',
                   'finding': 'explicit FIXED re-reserves source only on host success; nonfixed MAYMOVE uses host FIXED and re-reserves source regardless of host failure when reserved_va is enabled'},
                  {'url': 'https://raw.githubusercontent.com/qemu/qemu/v11.0.0/linux-user/main.c',
                   'functions': ['handle_arg_reserved_va', 'main'],
                   'finding': '-R 0 means default, and 32-bit guests on 64-bit hosts default to reserved_va; it does not disable guest address reservation'}],
              'inplace_growth_success_verified': False,
              'inplace_growth_limitation': 'QEMU 11 reserved_va host holes prevent non-MAYMOVE expansion (ENOMEM)',
              'production_shim_modified': False, 'main_camera_restarted': False,
              'stdout': process.stdout.decode('utf-8', 'replace'),
              'stderr': process.stderr.decode('utf-8', 'replace')}
    for name, data in [('stdout', process.stdout), ('stderr', process.stderr)]:
        (OUT / f'probe.{name}.log').write_bytes(data)
    (OUT / 'probe.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(report['stdout'])
    if process.returncode:
        print(report['stderr'])
    print('exit_code=' + str(process.returncode))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
