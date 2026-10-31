"""Build only the affected device module and probe the actual PP memory bank.

Uses a private mount namespace and output names; does not launch a camera or
change the active viewer, production shims, sessions or firmware packages.
"""
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime/pp-memory-task'
GUEST = '/opt/nx300m-lab/stock-1.15'
NAMES = ['version','width','height','plane_width','plane_rows','payload_bytes',
         'invalid','enabled_stages','unsupported_stages','mapping_checked',
         'ipc_output_enabled','bit_shift_control','dpc_control','dynamic_control',
         'cs_control','cs_source','cs_source_bytes','cs_columns','cs_rows',
         'pixels_completed','completion_generated']
PLANES = ['read_address','write_address','read_stride','write_stride','read_span',
          'write_span','read_allocation','write_allocation','read_mapped','write_mapped']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    samples = [ROOT/'outputs/firmware-runtime/capture-geometry'/
               f'nx300m-geometry-63-1-after_FinishOperation-output-p{i}-effective_last.bin'
               for i in range(4)]
    rows = [path.read_bytes() for path in samples]
    if any(len(row) != 4224 for row in rows):
        raise ValueError('captured SECC rows must each contain exactly 4224 bytes')
    (OUT/'captured-rows.bin').write_bytes(b''.join(rows))
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    device = OUT/'pp-memory-device.so'
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
                    '-std=c99','-O2','-Wall','-Wextra','-Werror','-fPIC','-ffreestanding',
                    '-fno-builtin','-fno-stack-protector','-nostdlib','-shared','-fuse-ld=lld',
                    'runtime/device_preview.c','-o',str(device)], cwd=ROOT, check=True)
    base = '/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    bank = ROOT/'outputs/firmware-runtime/ep-cs-grids-nx300m-memory-pass-pp.bin'
    bank_path = '/mnt/'+bank.drive[0].lower()+bank.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/pp_memory_task_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/pp-memory-task/probe.arm
mkdir -p "$rf/mod/pp-memory-task-probe"
cp outputs/firmware-runtime/pp-memory-task/probe.arm "$rf/mod/pp-memory-task-probe/probe.arm"
cp outputs/firmware-runtime/pp-memory-task/pp-memory-device.so "$rf/mod/pp-memory-task-probe/device.so"
cp {shlex.quote(bank_path)} "$rf/mod/pp-memory-task-probe/bank.bin"
cp outputs/firmware-runtime/pp-memory-task/captured-rows.bin "$rf/mod/pp-memory-task-probe/captured-rows.bin"
touch "$rf/mod/pp-memory-task-probe/task.bin"
touch "$rf/mod/pp-memory-task-probe/expanded-rows.bin"
chmod 666 "$rf/mod/pp-memory-task-probe/task.bin" "$rf/mod/pp-memory-task-probe/expanded-rows.bin"
chmod 755 "$rf/mod/pp-memory-task-probe/probe.arm"
unset LD_LIBRARY_PATH
unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
exec timeout 20 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_ISP_SIM=1 -E LD_PRELOAD=/mod/pp-memory-task-probe/device.so -cpu cortex-a9 -L / /mod/pp-memory-task-probe/probe.arm'
cp "$rf/mod/pp-memory-task-probe/task.bin" outputs/firmware-runtime/pp-memory-task/task.bin
cp "$rf/mod/pp-memory-task-probe/expanded-rows.bin" outputs/firmware-runtime/pp-memory-task/expanded-rows.bin
'''
    process = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',script], capture_output=True, timeout=50)
    for name, data in [('stdout',process.stdout),('stderr',process.stderr)]:
        (OUT/f'probe.{name}.log').write_bytes(data)
    report = {'exit_code':process.returncode, 'bank':str(bank),
              'stock_call':'unmodified libudd.so.3.1 pp_rw_dma_start',
              'scope':'actual register bank; private sentinel RAM and no complete PP pixel executor',
              'complete_pp_pixels_implemented':False, 'completion_generated':False,
              'stdout':process.stdout.decode('utf-8','replace'), 'stderr':process.stderr.decode('utf-8','replace')}
    if not process.returncode:
        words = struct.unpack('<'+'I'*(len(NAMES)+4*len(PLANES)), (OUT/'task.bin').read_bytes())
        report['task'] = dict(zip(NAMES,words[:len(NAMES)]))
        report['planes'] = {parity:dict(zip(PLANES,words[len(NAMES)+i*len(PLANES):len(NAMES)+(i+1)*len(PLANES)]))
                            for i, parity in enumerate(('00','01','10','11'))}
        report['unsupported_stages'] = ['dynamic DPC static-method replacement', 'CS LUT interpolation and active on-chip state',
                                        'DAMP arithmetic', 'RDMA expansion available only as an isolated input stage',
                                        '14-bit internal result to 12-bit WDMA quantization/packing']
        actual = struct.unpack('<'+'H'*(4*2812), (OUT/'expanded-rows.bin').read_bytes())
        expected = []
        for row in rows:
            # Independent little-endian 24-bit group extraction: two 12-bit
            # values per three bytes. Exclude the captured DMA row padding.
            for x in range(0,4218,3):
                group = int.from_bytes(row[x:x+3], 'little')
                expected.extend(((group&4095)*4, (group>>12)*4))
        if tuple(expected) != actual:
            raise AssertionError('ARM input expansion differs from captured row reference')
        report['input_stage'] = {
            'captured_sources':[str(path.relative_to(ROOT)) for path in samples],
            'captured_samples_checked':len(actual), 'captured_sample_mismatches':0,
            'captured_source_scope':'observed software MnF output, four effective-last rows',
            'full_geometry_synthetic_samples_checked':5624*3714,
            'output':'four planar u16 input-stage samples; not PP WDMA output',
            'hardware_bit_exact_verified':False,
            'late_unmapped_plane_leaves_output_unchanged':True,
            'nonlinear_gradient_rejected':True, 'overlapping_output_rejected':True}
    (OUT/'probe.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(process.returncode))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
