"""Execute unchanged NX500 interpolation and soft-float code at original VAs."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-only', action='store_true')
    parser.add_argument('--compare-target', action='store_true', help='Compare original NX300M interpolator on the same source knots')
    args = parser.parse_args()
    stem = 'nx500-contrast-target-probe' if args.compare_target else 'nx500-contrast-reference' if args.reference_only else 'nx500-contrast-probe'
    source = ROOT / 'work/reference/nx500-stock-1.12/partition-05.bin'
    raw = source.read_bytes()
    identity = json.loads((OUT/'nx500-pw-source.json').read_text())['source']['sha256']
    if hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError('NX500 P7 differs from verified reference')
    spans = [(0x3887f0,0x388ca8,'.nx500_interp'), (0x4fa6f0,0x4fadfc,'.nx500_float')]
    assembly = ['.syntax unified','.arm']
    for start, end, section in spans:
        # Preserve all original relative calls. Every direct branch must stay
        # in one of these two copied ranges; there are no PC data accesses.
        for ins in decode_span(raw,start,end):
            if 'pc' in ins.op_str and ins.mnemonic != 'pop':
                raise ValueError(f'Unexpected PC access at {ins.address:#x}')
            if ins.mnemonic.startswith('b') and ins.op_str.startswith('#'):
                target = int(ins.op_str[1:],16)-0x80000000
                if not any(a <= target < b for a,b,_ in spans):
                    raise ValueError(f'Branch outside copied code at {ins.address:#x}')
        assembly += [f'.section {section},"ax",%progbits','.balign 4']
        if section == '.nx500_interp':
            assembly += ['.global nx500_original_interpolate','.type nx500_original_interpolate,%function','nx500_original_interpolate:']
        assembly += [f'.incbin "{source.relative_to(ROOT).as_posix()}", {start}, {end-start}']
    assembly += ['.section .note.GNU-stack,"",%progbits']
    (OUT/'nx500-contrast-original.S').write_text('\n'.join(assembly)+'\n')
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    base, rootfs = wsl_path(ROOT), wsl_path(config['rootfs'])
    define = '' if args.reference_only else '-DVERIFY_PORT'
    extra_link = ''
    if args.compare_target:
        define += ' -DCOMPARE_TARGET'
        extra_link = '-Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so "$rf/lib/libdl.so.2"'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -ffp-contract=off -fno-fast-math -fexcess-precision=standard {define} \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \\
 -Wl,--section-start=.nx500_interp=0x803887f0 -Wl,--section-start=.nx500_float=0x804fa6f0 \\
 runtime/nx500_contrast_probe.c outputs/metol/nx500-contrast-original.S \\
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 {extra_link} "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/nx500-contrast-probe.arm
'''
    build = subprocess.run(['wsl.exe','-u','root','-e','sh','-c',script], capture_output=True,timeout=60)
    (OUT/'nx500-contrast-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'Build exit {build.returncode}: {build.stderr.decode("utf-8","replace")}')
    evidence = json.loads((OUT/'nx500-contrast.json').read_text())
    knots = evidence['contrast_knots']
    x, rows = knots['x'], [row['y'] for row in knots['rows']]
    inputs, cases = [], []
    # Exhaust the integer GammaY domain plus the negative values present in
    # the original static bases and a short upper extrapolation interval.
    for level, y in enumerate(rows):
        for query in range(-16,1040):
            inputs.append(' '.join(map(str,[7,query,*x,*y])))
            cases.append((level,query))
    run = subprocess.run(['wsl.exe','-u','root','-e',rootfs+'/qemu-arm-static','-L',rootfs,
                          base+'/outputs/metol/nx500-contrast-probe.arm'],
                         input=('\n'.join(inputs)+'\n').encode(),capture_output=True,timeout=60)
    (OUT/(stem+'.stderr.log')).write_bytes(run.stderr)
    values = [int(line) for line in run.stdout.splitlines()]
    completed = run.returncode == 0 and len(values) == len(cases)
    report = dict(build_exit_code=build.returncode,exit_code=run.returncode,
                  reference_only=args.reference_only,expected_cases=len(cases),observed_cases=len(values),
                  completed=completed,port_matches_original=completed and not args.reference_only,
                  target_compared=args.compare_target,target_matches_original=completed if args.compare_target else None,
                  query_domain=[-16,1039],original_p7_sha256=identity,
                  original_execution='Unchanged interpolation and all six soft-float dependencies at original VAs in isolated ARM ELF',
                  scope='21 seven-knot rows over integer GammaY inputs; no complete Gamma builder, hardware LUT or pixels',
                  first_unfinished_case=cases[len(values)] if len(values)<len(cases) else None,
                  stderr=run.stderr.decode('utf-8','replace').strip(),image_verified=False)
    (OUT/(stem+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    (OUT/(stem+'-values.json')).write_text(json.dumps(values)+'\n')
    print(json.dumps(report,indent=2))
    if not completed: raise SystemExit(run.returncode or 1)


if __name__ == '__main__':
    main()
