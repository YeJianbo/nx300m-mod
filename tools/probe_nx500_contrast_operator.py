"""Check all stored knots and one ARM preview quantization vector."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import subprocess

from build_ycc_probe import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/metol'


def main():
    source = ROOT/'work/reference/nx500-stock-1.12/partition-05.bin'
    raw = source.read_bytes()
    identity = json.loads((OUT/'nx500-pw-source.json').read_text())['source']['sha256']
    if hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError('P7 differs from the verified original firmware reference')
    evidence = json.loads((OUT/'nx500-contrast.json').read_text())['contrast_knots']
    header = (ROOT/'runtime/nx500_contrast_operator.h').read_text()

    def stored(name):
        match = re.search(r'static const int '+name+r'\[[^;]*?=\s*\{(.*?)\};', header, re.S)
        if not match:
            raise ValueError(f'Missing table {name}')
        return [int(value) for value in re.findall(r'-?\d+',match.group(1))]

    x, flat_y = stored('nx500_contrast_x'), stored('nx500_contrast_y')
    expected_y = [value for row in evidence['rows'] for value in row['y']]
    original_x = list(struct.unpack_from('<7i', raw, int(evidence['x_address'],16)-0x80000000))
    original_y = list(struct.unpack_from('<147i', raw, int(evidence['y_address'],16)-0x80000000))
    if x != evidence['x'] or flat_y != expected_y or x != original_x or flat_y != original_y:
        raise ValueError('Operator knots differ from extraction JSON or original P7 bytes')
    spans = [(0x3887f0,0x388ca8,'.nx500_interp'),(0x4fa6f0,0x4fadfc,'.nx500_float')]
    assembly = ['.syntax unified','.arm']
    for start,end,section in spans:
        assembly += [f'.section {section},"ax",%progbits','.balign 4']
        if section == '.nx500_interp':
            assembly += ['.global nx500_original_interpolate',
                         '.type nx500_original_interpolate,%function','nx500_original_interpolate:']
        assembly += [f'.incbin "{source.relative_to(ROOT).as_posix()}", {start}, {end-start}']
    assembly += ['.section .note.GNU-stack,"",%progbits']
    (OUT/'nx500-contrast-operator-original.S').write_text('\n'.join(assembly)+'\n')
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    base, rootfs = wsl_path(ROOT), wsl_path(config['rootfs'])
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -ffp-contract=off -fno-fast-math -fexcess-precision=standard \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \\
 -Wl,--section-start=.nx500_interp=0x803887f0 -Wl,--section-start=.nx500_float=0x804fa6f0 \\
 runtime/nx500_contrast_operator_probe.c outputs/metol/nx500-contrast-operator-original.S \\
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/nx500-contrast-operator-probe.arm
'''
    build = subprocess.run(['wsl.exe','-u','root','-e','sh','-c',script],capture_output=True,timeout=60)
    (OUT/'nx500-contrast-operator-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'ARM build exit {build.returncode}: {build.stderr.decode("utf-8","replace")}')
    run = subprocess.run(['wsl.exe','-u','root','-e',rootfs+'/qemu-arm-static','-L',rootfs,
                          base+'/outputs/metol/nx500-contrast-operator-probe.arm'],capture_output=True,timeout=30)
    (OUT/'nx500-contrast-operator.stdout.log').write_bytes(run.stdout)
    (OUT/'nx500-contrast-operator.stderr.log').write_bytes(run.stderr)
    rows = [list(map(int,line.split())) for line in run.stdout.splitlines()
            if len(line.split()) == 4 and line.split()[0].lstrip(b'-').isdigit()]
    capture = [list(map(int,line.split()[1:])) for line in run.stdout.splitlines()
               if line.startswith(b'capture ')]
    passed = run.returncode == 0 and len(rows) == 15 and len(capture) == 1
    report = {'build_exit_code':build.returncode,'exit_code':run.returncode,
              'all_21_tables_match_json_and_original_p7':True,
              'original_p7_sha256':identity,
              'original_execution':'Unchanged source interpolation and softfloat at original VAs in isolated ARM ELF.',
              'vector':{'level':19,'maximum':255,'count':15,
                        'fields':['input8','quantized_query10','original_arm_result10','quantized_result8'],
                        'values':rows},
              'separate_and_inplace_match_original':passed,
              'capture_upper_cap':{'fields':['input10','original_arm_unclipped','operator_result'],
                                   'values':capture,'passed':passed},
              'quantization':'Nearest integer with symmetric negative rounding, first v*1023/255 then source integer interpolation, upper cap1023, then result*255/1023.',
              'scope':'One preview15-point vector and one capture upper-cap input; source interpolation22176-point evidence is reused, not rerun. No Gamma-node/ISP/image verification.'}
    (OUT/'nx500-contrast-operator-probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not passed:
        raise SystemExit(run.returncode or 1)


if __name__ == '__main__':
    main()
