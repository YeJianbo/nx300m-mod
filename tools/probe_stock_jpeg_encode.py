"""Run original libsif EncodeJfif through its PLT into the optional CPU hook.

Uses generated YCC and private callbacks in a fresh ARM process, never the main
camera process. Run build_jpeg_encode.py before this bounded caller probe.
"""
from __future__ import annotations

import json
import shlex
import subprocess

from build_jpeg_encode import ROOT, OUT, StockElf, wsl_path, Cs, CS_ARCH_ARM, CS_MODE_ARM


def main() -> int:
    if not (OUT/'jpeg-encode.so').is_file():
        raise RuntimeError('First run tools/build_jpeg_encode.py')
    sif = StockElf('usr/lib/libsif.so')
    symbols = sif.elf.get_section_by_name('.dynsym')
    relocations = sif.elf.get_section_by_name('.rel.plt')
    name = '_ZN7DscJfif10JpegEncodeER7sJpegOpb'
    slots = [r['r_offset'] for r in relocations.iter_relocations()
             if symbols.get_symbol(r['r_info_sym']).name == name]
    if len(slots) != 1:
        raise RuntimeError('Expected one original JpegEncode JUMP_SLOT')
    slot = slots[0]
    listing = []
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    udd = StockElf('usr/lib/libudd.so.3.1')
    for elf, start, size, description in (
            (sif, 0x287b0, 0x64, 'GetInstance allocates and constructs original 20-byte object'),
            (sif, 0x28c50, 0x32c, 'original EncodeJfif task construction, PLT call, EOI scan and length result'),
            (sif, 0x28b5c, 0x20, 'JpegEncode loads deltaY/deltaC unchanged'),
            (sif, 0x298f8, 0x10, 'HAL copies both deltas unchanged into libudd request'),
            (udd, 0xd2d18, 0x134, 'FRAME input: odd chroma=start+deltaC; hardware deltaC=2*deltaC')):
        listing.append(f'\n## {elf.name}: {description}')
        listing.extend(f'{i.address:08x} {i.mnemonic:8s} {i.op_str}'
                       for i in decoder.disasm(elf.read(start, size), start))
    (OUT/'stock-call-evidence.asm').write_text('\n'.join(listing)+'\n', encoding='utf-8')
    script = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -march=armv7-a -mfloat-abi=soft -fno-stack-protector \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -DSTOCK_JPEG_GOT={hex(slot)} -Iruntime \\
 -include outputs/firmware-runtime/jpeg-encode/stock_libc_compat.h \\
 -nostdlib -no-pie -fno-pic -nostartfiles -Wl,-e,_start -Wl,--export-dynamic \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/jpeg_encode_stock_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc \\
 -o outputs/firmware-runtime/jpeg-encode/jpeg-encode-stock-probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 exec timeout 20 "$1/qemu-arm-static" -cpu cortex-a9 -L "$1" \\
  -E LD_LIBRARY_PATH="$1/usr/lib:$1/lib" \\
  -E LD_PRELOAD="$2/outputs/firmware-runtime/jpeg-encode/jpeg-encode.so" \\
  "$2/outputs/firmware-runtime/jpeg-encode/jpeg-encode-stock-probe.arm" \\
  "$2/outputs/firmware-runtime/jpeg-encode"
' sh "$rf" {shlex.quote(wsl_path(ROOT))}
'''
    process = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-s'], input=script.encode(),
                             capture_output=True, timeout=50)
    (OUT/'stock-call.stdout.log').write_bytes(process.stdout)
    (OUT/'stock-call.stderr.log').write_bytes(process.stderr)
    report = {'exit_code': process.returncode, 'arm_exit_code': process.returncode, 'main_namespace_accessed': False,
              'camera_started': False, 'generated_input_only': True, 'jump_slot_offset': hex(slot),
              'stock_planar_mode1': 'not adapted: no proven implicit half-pitch interpretation; see stock-call-evidence.asm'}
    if process.returncode == 0:
        traces = [json.loads(line[len('[JPEG TASK] '):])
                  for line in process.stderr.decode('utf-8','replace').splitlines()
                  if line.startswith('[JPEG TASK] ')]
        if len(traces) != 2 or traces[-1]['layout_error'] or len(traces[-1]['ranges']) != 3:
            raise RuntimeError('Original caller trace did not describe the exact generated mode2 task')
        if [item['length'] for item in traces[-1]['ranges']] != [12288,12288,6144]:
            raise RuntimeError('Task descriptor spans disagree with generated memory layout')
        report['task_traces'] = traces
        lines = process.stdout.decode('utf-8', 'replace').splitlines()
        report['call'] = json.loads(next(line for line in lines if line.startswith('{"stock_caller"')))
        from PIL import Image, JpegImagePlugin
        with Image.open(OUT/'stock-encodejfif-mode2.jpg') as image:
            image.load()
            tables = json.loads((OUT/'quant-tables.json').read_text())['tables'][0]['natural_order_tables']
            if (image.size != (128, 96) or JpegImagePlugin.get_sampling(image) != 2
                    or [image.quantization[i] for i in (0, 1)] != tables):
                raise RuntimeError('Real caller JPEG has incorrect dimensions, sampling, or DQT')
            report['decode'] = {'dimensions': list(image.size), 'sampling': 420,
                                'stock_index10_dqt_equal': True, 'rgb_extrema': image.getextrema()}
        # Decode directly to YCbCr: RGB clipping would hide or introduce sample
        # differences. The smooth generated chroma needs only tiny JPEG/upsample
        # rounding error. This detects DCT-size/raw-input mismatches that valid
        # SOF/DQT/EOI and successful file decoding do not reveal.
        with Image.open(OUT/'stock-encodejfif-mode2.jpg') as image:
            image.draft('YCbCr', image.size)
            image.load()
            if image.mode != 'YCbCr':
                raise RuntimeError('Decoder did not preserve direct YCbCr output')
            maximum_error, total_error = [0, 0, 0], [0, 0, 0]
            for row in range(96):
                for x in range(128):
                    expected = (32+(x+row)%192, 96+x//4, 144-row//4)
                    actual = image.getpixel((x, row))
                    for component in range(3):
                        error = abs(actual[component]-expected[component])
                        maximum_error[component] = max(maximum_error[component], error)
                        total_error[component] += error
            report['generated_pixel_check'] = {
                'direct_ycbcr_decode': True, 'max_abs_error': maximum_error,
                'mean_abs_error': [value/(128*96) for value in total_error],
                'maximum_allowed_for_this_smooth_fixture': [2, 3, 3]}
            if any(actual > allowed for actual, allowed in zip(maximum_error, (2, 3, 3))):
                report['exit_code'] = 1
                report['validation_error'] = 'Generated YCC pixel error exceeds bounded fixture tolerance'
    else:
        print(process.stderr.decode('utf-8', 'replace'))
    (OUT/'stock-call-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
