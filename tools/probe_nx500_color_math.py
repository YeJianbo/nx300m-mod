"""Build ARM C color arithmetic and compare every source table against its transcription."""
import json
import hashlib
import re
from pathlib import Path
import shlex
import subprocess

from build_ycc_probe import wsl_path
from inspect_nx500_pw import multiply_q8, chroma_mix_and_saturation, decode_span

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/metol'


def main():
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    evidence = json.loads((OUTPUT / 'nx500-pw-source.json').read_text(encoding='utf-8'))
    operators = evidence['source_color_operators']
    firmware = PROJECT / evidence['source']['file']
    raw = firmware.read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence['source']['sha256']:
        raise ValueError('Original P7 identity differs from inspected source')
    # Both leaves use only caller pointers, stack memory and internal relative
    # branches. Confirm this before placing their unchanged bytes in the ELF.
    assembly = ['.syntax unified', '.arm', '.text']
    source_path = firmware.relative_to(PROJECT).as_posix()
    for name, start, end in (('nx500_original_multiply', 0x381c84, 0x381d40),
                              ('nx500_original_saturation', 0x381df0, 0x381f28)):
        instructions = decode_span(raw, start, end)
        for ins in instructions:
            if re.search(r'\bpc\b', ins.op_str):
                raise ValueError(f'Leaf has a PC operand: {ins.address:#x}')
            if ins.mnemonic == 'bx' and ins.op_str == 'lr': continue
            if ins.mnemonic.startswith('b'):
                if ins.mnemonic != 'bne' or not start + 0x80000000 <= int(ins.op_str[1:], 16) < end + 0x80000000:
                    raise ValueError(f'Leaf has an unsupported branch: {ins.address:#x}')
        assembly += ['.balign 4', f'.global {name}', f'.type {name}, %function',
                     f'{name}:', f'.incbin "{source_path}", {start}, {end-start}',
                     f'.size {name}, .-{name}']
    assembly += ['.section .note.GNU-stack,"",%progbits']
    (OUTPUT / 'nx500-color-original.S').write_text('\n'.join(assembly)+'\n', encoding='ascii')
    bases = [
        [77, 150, 29, -43, -85, 128, 128, -107, -21],
        [256, 0, 0, -2, 1, 1, -1, 2, -1],
        [32767, -32768, 123, -32768, 32767, -123, 32767, -32768, 321],
    ]
    inputs, expected, labels = [], [], []
    for entry in operators['hue']['entries']:
        for index, base in enumerate(bases):
            matrix = entry['matrix_q8']
            inputs.append([0, *matrix, *base])
            expected.append(multiply_q8(matrix, base))
            labels.append(f'hue={entry["value"]},base={index}')
    inputs.append([0, *([32767] * 18)])
    expected.append(multiply_q8([32767] * 9, [32767] * 9))
    labels.append('dense-signed32-overflow')
    for entry in operators['saturation']['entries']:
        for variant, (mix, offsets) in enumerate(zip(
                operators['chroma_mix_variants']['entries'],
                operators['chroma_row_sum_offsets']['entries'])):
            for index, base in enumerate(bases):
                gain = entry['gain_q5']
                inputs.append([1, *base, *mix, *offsets, gain])
                expected.append(chroma_mix_and_saturation(base, mix, gain, offsets))
                labels.append(f'sat={entry["value"]},variant={variant},base={index}')
    # Compose the new production helper against original leaves, with tables
    # taken independently from the inspected firmware report.
    live_base = [77, 150, 29, -47, -91, 138, 138, -115, -23]
    for sat in range(21):
        hue, variant, offset, delta = 20-sat, sat % 7, (sat+3) % 7, sat % 3
        rotation = operators['hue']['entries'][hue]['matrix_q8']
        mix = operators['chroma_mix_variants']['entries'][variant]
        offsets = operators['chroma_row_sum_offsets']['entries'][offset]
        gain = operators['saturation']['entries'][sat]['gain_q5'] + delta
        inputs.append([2, *live_base, hue, sat, variant, offset, delta,
                       *rotation, *mix, *offsets, gain])
        expected.append(multiply_q8(rotation, chroma_mix_and_saturation(live_base, mix, gain, offsets)))
        labels.append(f'complete-operator:hue={hue},sat={sat},mix={variant},offset={offset},delta={delta}')
    rootfs, base = wsl_path(config['rootfs']), wsl_path(PROJECT)
    build = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \\
 runtime/nx500_color_math_probe.c outputs/metol/nx500-color-original.S \\
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/nx500-color-math-probe.arm
'''
    compiled = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', build],
                              capture_output=True, timeout=60)
    (OUTPUT / 'nx500-color-math-build.log').write_bytes(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        raise RuntimeError(f'ARM build exit {compiled.returncode}: {compiled.stderr.decode("utf-8", "replace")}')
    run = subprocess.run(
        ['wsl.exe', '-u', 'root', '-e', rootfs + '/qemu-arm-static', '-L', rootfs,
         base + '/outputs/metol/nx500-color-math-probe.arm'],
        input=('\n'.join(' '.join(map(str, row)) for row in inputs) + '\n').encode('ascii'),
        capture_output=True, timeout=30)
    (OUTPUT / 'nx500-color-math.stdout.log').write_bytes(run.stdout)
    (OUTPUT / 'nx500-color-math.stderr.log').write_bytes(run.stderr)
    observed = [list(map(int, row.split())) for row in run.stdout.decode('ascii').splitlines()]
    mismatch = [dict(case=label, expected=want, observed=got)
                for label, want, got in zip(labels, expected, observed) if want != got]
    report = dict(build_exit_code=compiled.returncode, exit_code=run.returncode,
                  expected_cases=len(expected), observed_cases=len(observed), mismatches=mismatch,
                  transcription_match=run.returncode == 0 and len(observed) == len(expected) and not mismatch,
                  original_leaf_execution='Unchanged position-independent original leaf bytes in ARM ELF, QEMU user mode',
                  original_leaf_verified=run.returncode == 0 and len(observed) == len(expected) and not mismatch,
                  original_p7_sha256=evidence['source']['sha256'],
                  composition_cases=21,
                  scope='Portable C leaves and complete composition helper versus Python and two original ARM arithmetic leaves; no camera, MMIO, mode dispatch or ISP pixels executed',
                  camera_or_full_vm_started=False, image_verified=False)
    (OUTPUT / 'nx500-color-math-probe.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not report['transcription_match']: raise SystemExit(1)


if __name__ == '__main__':
    main()
