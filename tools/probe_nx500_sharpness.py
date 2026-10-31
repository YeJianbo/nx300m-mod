"""Compare portable gain math against unchanged NX500 ARM instruction blocks."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from build_ycc_probe import wsl_path
from inspect_nx500_pw import decode_span

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/metol'


def main():
    source = ROOT / 'work/reference/nx500-stock-1.12/partition-05.bin'
    raw = source.read_bytes()
    evidence = json.loads((OUT/'nx500-sharpness.json').read_text())
    identity = hashlib.sha256(raw).hexdigest()
    if identity != evidence['source_sha256']:
        raise ValueError('NX500 P7 differs from verified reference')
    assembly = ['.syntax unified', '.arch armv7-a', '.arm', '.text']
    spans = []

    def block(start, end):
        # Copied internal branch displacements remain valid after relocation.
        for ins in decode_span(raw, start, end):
            if 'pc' in ins.op_str:
                raise ValueError(f'Unexpected PC access at {ins.address:#x}')
            if ins.mnemonic.startswith('b') and ins.op_str.startswith('#'):
                target = int(ins.op_str[1:], 16) - 0x80000000
                if not start <= target <= end:
                    raise ValueError(f'External branch at {ins.address:#x}')
        spans.append([hex(0x80000000+start), hex(0x80000000+end)])
        assembly.append(f'.incbin "{source.relative_to(ROOT).as_posix()}", {start}, {end-start}')

    def entry(name):
        assembly.extend(['.balign 4', f'.global {name}', f'.type {name},%function', name+':'])

    entry('original_percent')
    assembly.extend(['push {r7,lr}', 'mov r7,r1', 'mov r1,r0'])
    block(0x3a252c, 0x3a2530)
    block(0x3a2540, 0x3a2544)
    block(0x3a254c, 0x3a2554)
    block(0x3a2570, 0x3a2574)
    assembly.extend(['mov r0,r1', 'pop {r7,pc}'])
    entry('original_gain8')
    assembly.extend(['push {r4,lr}', 'mov lr,r0'])
    block(0x3a25d8, 0x3a25f8)
    assembly.extend(['mov r0,ip', 'pop {r4,pc}'])
    entry('original_gain7')
    assembly.append('push {r6,lr}')
    block(0x3a2600, 0x3a2620)
    assembly.extend(['mov r0,r6', 'pop {r6,pc}'])
    entry('original_pack7')
    assembly.extend(['push {r6,lr}', 'mov ip,r0', 'mov r6,r1'])
    block(0x3a2634, 0x3a2638)
    assembly.extend(['mov r0,ip', 'pop {r6,pc}', '.section .note.GNU-stack,"",%progbits'])
    (OUT/'nx500-sharpness-original.S').write_text('\n'.join(assembly)+'\n')
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    base, rootfs = wsl_path(ROOT), wsl_path(config['rootfs'])
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \
 runtime/nx500_sharpness_probe.c outputs/metol/nx500-sharpness-original.S \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/nx500-sharpness-probe.arm
'''
    build = subprocess.run(['wsl.exe','-u','root','-e','sh','-c',script], capture_output=True, timeout=60)
    (OUT/'nx500-sharpness-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'Build exit {build.returncode}: {build.stderr.decode("utf-8", "replace")}')
    # Exhaust byte-sized bases at every source level. Additional vectors cover
    # percentage truncation, signed multiplication and 32-bit wrap boundaries.
    factors = evidence['sharpness_factors']['values']
    cases = [(factor, percent, gain, old) for factor in factors
             for percent in (0, 1, 99, 100, 101, 200, 0xffffffff)
             for gain in (*range(256), 0x7fffffff, 0x80000000, 0xffffffff)
             for old in (0, 128)]
    cases += [(256,100,gain,255) for gain in (32767,32768,65535,65536,8388607,8388608,16777215)]
    run = subprocess.run(['wsl.exe','-u','root','-e',rootfs+'/qemu-arm-static','-L',rootfs,
                          base+'/outputs/metol/nx500-sharpness-probe.arm'],
                         input=('\n'.join(' '.join(map(str,c)) for c in cases)+'\n').encode(),
                         capture_output=True, timeout=60)
    (OUT/'nx500-sharpness-probe.stderr.log').write_bytes(run.stderr)
    completed = run.returncode == 0 and run.stdout.strip() == str(len(cases)).encode()
    report = dict(build_exit_code=build.returncode, exit_code=run.returncode,
                  expected_cases=len(cases), stdout=run.stdout.decode().strip(),
                  port_matches_original=completed, original_p7_sha256=identity,
                  original_instruction_spans=spans,
                  scope='Isolated unchanged ARM percentage, 8/7-bit gain and BFI blocks with ABI wrappers; not the full EE node.',
                  target_mapping_verified=False, image_verified=False,
                  stderr=run.stderr.decode('utf-8','replace').strip())
    (OUT/'nx500-sharpness-probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not completed:
        raise SystemExit(run.returncode or 1)


if __name__ == '__main__':
    main()
