"""Execute the production ready-gate A32 bridges in an isolated QEMU process.

Lens-state and PP submission are fixtures. This verifies registers, flags,
fallback and rebuild decisions, not sensor color or the real hardware pipeline.
"""
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens'

FIXTURE = r'''
static int fake_ready;
#define NX_MANUAL_LENS_CS_ABI_PROBE 1
#include "runtime/manual_lens_cs.c"
static void *fake_operation=(void *)1;
static unsigned int module_ready(void *operation) {
    return operation && fake_ready==1?0x20000019u:0x20000018u;
}
static unsigned int capture_gain=0x100;
static unsigned int get_gain(void) { return capture_gain; }
static unsigned char fixture_lens[40];
static unsigned int init_calls, init_ok=1;
static void initialize(void *opaque) {
    unsigned char *pp=opaque;
    ++init_calls;
    if(effective_ready(fixture_lens,0,(void *)0)!=0) { init_ok=0; return; }
    note_init_result(init_ok?0:-5,pp);
    /* A failed stock upload leaves an earlier successful bit4 untouched. */
    *(unsigned int *)(pp+0x18)|=init_ok?5:1;
}

extern void invoke_gate(unsigned int *, const void *, const void *);
extern void gate_resume(void);
/* Store the observed register file before using any scratch registers. */
__asm__(
".text\n.arm\n.global invoke_gate\n.type invoke_gate,%function\n"
"invoke_gate:\n"
"push {r4-r11,lr}\nsub sp,sp,#52\n"
"str r0,[sp]\nstr r1,[sp,#4]\nstr r2,[sp,#8]\n"
"str r2,[sp,#32]\n"
"mov r1,sp\nstr r1,[r0,#64]\n"
"mov r0,#0x10\nmov r1,#0x11\nmov r2,#0x12\nmov r3,#0x13\n"
"mov r4,#0x14\nmov r5,#0x15\nldr r6,[sp,#8]\nmov r7,#0x17\n"
"mov r8,#0x18\nldr r9,[sp,#8]\nmov r10,#0x1a\nmov r11,#0x1b\n"
"mov r12,#0x1c\nmov lr,#0x1e\nldr pc,[sp,#4]\n"
".global gate_resume\ngate_resume:\n"
"push {r0-r12,lr}\nmrs r1,cpsr\nldr r0,[sp,#56]\n"
"str r1,[r0,#56]\nadd r1,sp,#56\nstr r1,[r0,#60]\n"
"mov r1,sp\nmov r2,#56\nbl memcpy\n"
"add sp,sp,#56\nadd sp,sp,#52\npop {r4-r11,pc}\n"
".size invoke_gate,.-invoke_gate\n"
".global _start\n.type _start,%function\n_start:\nbl probe_main\nmov r7,#1\nsvc #0\n");

static int exercise(unsigned int *gate, unsigned char *lens, unsigned int expected) {
    unsigned int observed[17];
    invoke_gate(observed,gate,lens);
    for(unsigned int i=0;i<13;i++) {
        unsigned int wanted=i==3?expected:(i==6||i==9)?(unsigned int)lens:0x10+i;
        if(observed[i]!=wanted) return 10+(int)i;
    }
    if(observed[13]!=0x1e) return 24;
    if((observed[14]&0xf0000000u)!=(expected?0x20000000u:0x60000000u)) return 25;
    if(observed[15]!=observed[16]) return 26;
    return 0;
}

int probe_main(void) {
    unsigned int *code=mmap((void *)0,4096,3,0x22,-1,0);
    if(code==(void *)-1) return 2;
    stock_module_ready=module_ready; stock_operation=&fake_operation;
    stock_capture_gain=get_gain;
    fill_gate(code,6,0,(unsigned int)gate_resume);
    fill_gate(code+16,9,1,(unsigned int)gate_resume);
    fill_result_gate(code+32,(unsigned int)gate_resume);
    flush_code(code,code+42);
    if(mprotect(code,4096,5)) return 3;
    unsigned int lens_words[10]={0};
    unsigned char *lens=(unsigned char *)lens_words;
    lens_words[8]=1;
    for(unsigned int phase=0;phase<2;phase++) {
        unsigned int *gate=code+16*phase;
        for(unsigned int ready=0;ready<3;ready++) {
            lens[8]=ready;
            for(unsigned int scenario=0;scenario<3;scenario++) {
                fake_ready=(int)scenario;
                fake_operation=scenario==2?(void *)0:(void *)1;
                unsigned int expected=scenario==1?ready:0;
                int result=exercise(gate,lens,expected);
                if(result) return result;
            }
        }
    }
    if(checked[0]!=9 || checked[1]!=9 || suppressed[0]!=7 || suppressed[1]!=7) return 30;
    unsigned int observed[17];
    repair_owner=(unsigned int)lens;
    invoke_gate(observed,code+32,lens);
    for(unsigned int i=0;i<13;i++) {
        unsigned int wanted=i==0?observed[16]+0x7c:i==5?0x10:
            (i==6||i==9)?(unsigned int)lens:0x10+i;
        if(observed[i]!=wanted) return 31;
    }
    if(init_result!=0x10 || observed[13]!=0x1e || observed[15]!=observed[16]) return 32;
    repair_owner=0;
    /* Exercise the actual repair policy separately from the register sentinels.
     * The stock initializer is a fixture, including its nested phase-0 gate. */
    unsigned int pp_words[24]={0};
    unsigned char *pp=(unsigned char *)pp_words;
    stock_init=initialize; fake_ready=0; fake_operation=(void *)1;
    *(unsigned int *)(fixture_lens+0x20)=1;
    fixture_lens[8]=1; /* stale IQ ready with no electronic lens */
    if(effective_ready(fixture_lens,1,pp)!=0 || init_calls!=1) return 40;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=1) return 41; /* unchanged settings do not rebuild each frame */
    ++capture_gain;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=2) return 42;
    pp_words[0x44/4]=1818;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=3) return 43;
    fake_ready=1;
    if(effective_ready(fixture_lens,1,pp)!=1 || init_calls!=3) return 44;
    fake_ready=0; pp_words[0x18/4]=7;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=3 || deferred!=1) return 45;
    pp_words[0x18/4]=5;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=4) return 46;
    init_ok=0;
    ++capture_gain;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=5 || default_owner!=0) return 47;
    init_ok=1;
    fixture_lens[8]=0; fake_ready=1;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=6 || rebuilt!=5) return 48;
    fixture_lens[8]=1;
    *(unsigned int *)(fixture_lens+0x20)=0;
    if(effective_ready(fixture_lens,1,pp)!=0 || init_calls!=6) return 49;
    pp_words[0x2c/4]=4;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=6) return 50;
    pp_words[0x2c/4]=0xffffffffu;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=6) return 51;
    pp_words[0x2c/4]=0;
    effective_ready(fixture_lens,1,pp);
    if(init_calls!=7 || rebuilt!=6) return 52;
    return munmap(code,4096)?49:0;
}
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'cs-gate-fixture.c').write_text(FIXTURE, encoding='ascii')
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    compiler = shutil_which_clang()
    subprocess.run([
        compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
        '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
        '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-pie',
        '-ffunction-sections', '-fdata-sections', '-I.', '-I' + str(OUT),
        '-c', str(OUT/'cs-gate-fixture.c'), '-o', str(OUT/'cs-gate-fixture.o'),
    ], cwd=PROJECT, check=True)
    # Link against the same old ARM libc used by the verified stock root.
    script = f'''set -eu
cd {shlex.quote(base)}
guest=/opt/nx300m-lab/stock-1.15
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-ld" -m armelf_linux_eabi --gc-sections -e _start \\
 --dynamic-linker /lib/ld-linux.so.3 -rpath-link "$guest/lib" \\
 outputs/controls/manual-lens/cs-gate-fixture.o "$guest/lib/libc.so.6" \\
 -o outputs/controls/manual-lens/cs-gate-fixture.arm
timeout 10 "$guest/qemu-arm-static" -L "$guest" outputs/controls/manual-lens/cs-gate-fixture.arm
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-c', script],
                            capture_output=True, timeout=30)
    report = {
        'scope': 'production A32 gates and repair policy; simulated electronic state and stock initializer',
        'bridge_cases': 19, 'repair_sequence_checks': 13,
        'phases': ['InitCsParam 2D', 'UpdateCsParam 2D'],
        'checks': ['r0-r12 and LR preservation except effective-ready r3',
                   'SP and CMP NZCV for ready gates; LUT-result bridge MOV/ADD replay',
                   'electronic ready, not ready, absent instance; stale/empty IQ ready and shading',
                   'nested default init; unchanged settings reuse; gain/geometry refresh',
                   'electronic lens restoration; DMA deferral; failed init not cached'],
        'returncode': result.returncode,
        'stdout': result.stdout.decode('utf-8', 'replace'),
        'stderr': result.stderr.decode('utf-8', 'replace'),
        'physical_color_verified': False,
    }
    (OUT/'cs-gate-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


def shutil_which_clang():
    import shutil
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    return compiler


if __name__ == '__main__':
    main()
