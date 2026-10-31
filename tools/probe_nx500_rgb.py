"""Check source RGB column arithmetic against unchanged original ARM bytes."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from build_ycc_probe import wsl_path
from inspect_nx500_pw import decode_span

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/metol'


def main():
    evidence=json.loads((OUT/'nx500-pw-source.json').read_text())
    source=ROOT/evidence['source']['file']
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=evidence['source']['sha256']:
        raise ValueError('Original NX500 P7 identity differs')
    instructions=[(0x3832c4,'mul','r7, r7, r3'),(0x3832fc,'add','r7, r7, #0x40'),
                  (0x383324,'asr','r7, r7, #7'),(0x38334c,'strh','r7, [r4, #0x22]')]
    assembly=['.syntax unified','.arch armv7-a','.arm','.text','.balign 4',
              '.global nx500_original_rgb_coefficient','.type nx500_original_rgb_coefficient,%function',
              'nx500_original_rgb_coefficient:','push {r4,r7,r8,lr}','sub sp,sp,#48',
              'mov r4,sp','mov r7,r0','mov r3,r1']
    for offset,mnemonic,operands in instructions:
        ins=list(decode_span(raw,offset,offset+4))[0]
        if (ins.mnemonic,ins.op_str)!=(mnemonic,operands):
            raise ValueError(f'Unexpected original instruction at {offset:#x}')
        assembly.append(f'.incbin "{source.relative_to(ROOT).as_posix()}", {offset}, 4')
    assembly+=['ldrsh r0,[r4,#0x22]','add sp,sp,#48','pop {r4,r7,r8,pc}']
    start,end=0x388ea4,0x388f1c
    for ins in decode_span(raw,start,end):
        if 'pc' in ins.op_str:
            raise ValueError(f'Unexpected PC access in CCM leaf at {ins.address:#x}')
        if ins.mnemonic.startswith('b') and ins.op_str.startswith('#'):
            target=int(ins.op_str[1:],16)-0x80000000
            if not start<=target<end:
                raise ValueError(f'External branch in CCM leaf at {ins.address:#x}')
    assembly+=['.balign 4','.global nx500_original_ccm','.type nx500_original_ccm,%function',
               'nx500_original_ccm:',f'.incbin "{source.relative_to(ROOT).as_posix()}", {start}, {end-start}',
               '.section .rodata','.balign 2','.global nx500_original_rgb_final',
               'nx500_original_rgb_final:',f'.incbin "{source.relative_to(ROOT).as_posix()}", {0x62fd40}, 18',
               '.section .note.GNU-stack,"",%progbits']
    (OUT/'nx500-rgb-original.S').write_text('\n'.join(assembly)+'\n')
    config=json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    base,rootfs=wsl_path(ROOT),wsl_path(config['rootfs'])
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rootfs)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \
 runtime/nx500_rgb_probe.c outputs/metol/nx500-rgb-original.S \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/nx500-rgb-probe.arm
'''
    build=subprocess.run(['wsl.exe','-u','root','-e','sh','-c',script],capture_output=True,timeout=60)
    (OUT/'nx500-rgb-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'Build exit {build.returncode}: {build.stderr.decode("utf-8","replace")}')
    bases=[[77,150,29,-47,-91,138,138,-115,-23],
           [-32768,32767,-16385,16384,-129,129,-1,1,0],
           [-1024,-1023,1023,-257,-256,-255,63,64,65]]
    cases=[[-1,code,code,code,*matrix] for code in range(257) for matrix in bases]
    entries={entry['value']:entry['code_12bit'] for entry in evidence['rgb_encoding']['entries']}
    for value in range(201):
        cases.append([value,entries[value],entries[200-value],entries[100],*bases[0]])
    cases.extend([[-1,0,128,256,*matrix] for matrix in bases])
    run=subprocess.run(['wsl.exe','-u','root','-e',rootfs+'/qemu-arm-static','-L',rootfs,
                        base+'/outputs/metol/nx500-rgb-probe.arm'],
                       input=('\n'.join(' '.join(map(str,row)) for row in cases)+'\n').encode(),
                       capture_output=True,timeout=60)
    (OUT/'nx500-rgb-probe.stderr.log').write_bytes(run.stderr)
    passed=run.returncode==0 and run.stdout.strip()==str(len(cases)).encode()
    report=dict(build_exit_code=build.returncode,exit_code=run.returncode,passed=passed,
                matrix_cases=len(cases),coefficient_comparisons=len(cases)*9,
                inplace_checked=True,invalid_code_no_write_checked=passed,
                final_ccm_comparisons=len(cases)*9,final_ccm_leaf=['0x80388ea4','0x80388f1c'],
                final_transform_original_table='0x8062fd40',
                recipe_encoding_cases=201,recipe100_code=entries[100],reset_sentinel_code=128,
                original_instructions=[hex(0x80000000+row[0]) for row in instructions],
                original_p7_sha256=evidence['source']['sha256'],
                scope='Original MUL/ADD/ASR/STRH coefficients and original final CCM multiply leaf with original F table. Not the full base selection, color node or target adapter.',
                target_mapping_verified=False,image_verified=False,
                stdout=run.stdout.decode().strip(),stderr=run.stderr.decode('utf-8','replace').strip())
    (OUT/'nx500-rgb-probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not passed: raise SystemExit(run.returncode or 1)


if __name__=='__main__':
    main()
