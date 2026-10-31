"""Run the stock A32 Wi-Fi admission tail and production patch in QEMU.

Only 0x1cccb0..0x1ccd8f executes. Earlier storage/busy checks, real lens
hardware, RVF state transitions and the real camera process are not exercised.
"""
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from build_manual_lens_wifi import write_wifi_stock_header

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens'

FIXTURE = r'''
extern int strcmp(const char *, const char *);
extern void *memcpy(void *, const void *, unsigned int);
extern void *mmap(void *, unsigned int, int, int, int, int);
extern int printf(const char *, ...);
extern int fflush(void *);
#define dlsym probe_dlsym
#define getenv probe_getenv
#define readlink probe_readlink
#include "runtime/manual_lens_wifi_patch.h"
#undef dlsym
#undef getenv
#undef readlink

void *probe_dlsym(void *handle, const char *name) {
    (void)handle;
    return strcmp(name,NX_WIFI_GATE_SYMBOL) ? (void *)0 : (void *)NX_WIFI_GATE_ADDRESS;
}
char *probe_getenv(const char *name) {
    return strcmp(name,"NX300M_MANUAL_LENS_WIFI") ? (char *)0 : "1";
}
int probe_readlink(const char *path, char *out, unsigned int size) {
    const char expected[]="/usr/bin/di-camera-app-nx300";
    if(strcmp(path,"/proc/self/exe") || size<sizeof expected-1) return -1;
    memcpy(out,expected,sizeof expected-1);
    return sizeof expected-1;
}

static int next_mode,next_state,last_error,getter_calls,bad_calls;
static int getter(unsigned int id) {
    ++getter_calls;
    if(id==0x3ec) return next_mode;
    if(id==0xbb) return next_state;
    ++bad_calls; return -100;
}
static void notification(unsigned int kind,unsigned int code,unsigned int level) {
    if(kind!=10 || level!=3) ++bad_calls;
    last_error=(int)code;
}

extern int invoke_tail(void *,int,int,int);
/* Recreate only the original frame slots used by this tail. Its unmodified
 * epilogue restores FP/PC and returns through the frame built below. */
__asm__(
".text\n.arm\n.global invoke_tail\n.type invoke_tail,%function\n"
"invoke_tail:\npush {r4-r11,lr}\nsub sp,sp,#68\n"
"add fp,sp,#64\nstr r1,[fp,#-40]\nstr r3,[fp,#-44]\n"
"strb r2,[fp,#-9]\nmov ip,#0\nstr ip,[fp,#-8]\n"
"str ip,[fp,#-4]\nadr ip,1f\nstr ip,[fp]\nbx r0\n"
"1:\npop {r4-r11,pc}\n.size invoke_tail,.-invoke_tail\n"
".global _start\n.type _start,%function\n_start:\n"
"bl probe_main\nmov r7,#1\nsvc #0\n");

struct test_case {
    const char *name;
    int connection,kmount,lock,mode,state;
    int returned[2],error[2],calls[2];
};
static const struct test_case cases[]={
    {"physical_kmount_regression",1,1,0,2,0,{0,1},{0x61,0},{0,1}},
    {"disconnected_manual_lens",0,1,0,2,0,{0,1},{0x62,0},{0,1}},
    {"lenslock_retained",1,1,1,2,0,{0,0},{0x5c,0x5c},{0,0}},
    {"invalid_connection_two",2,1,0,2,0,{0,0},{0x62,0x62},{0,0}},
    {"invalid_connection_negative",-1,1,0,2,0,{0,0},{0x62,0x62},{0,0}},
    {"kmount_reaches_later_restriction",1,1,0,1,0,{0,0},{0x61,6},{0,2}},
    {"stock_later_restriction_retained",1,0,0,1,0,{0,0},{6,6},{2,2}},
    {"stock_later_allow_retained",1,0,0,1,1,{1,1},{0,0},{2,2}},
};

static int page(unsigned int address) {
    return mmap((void *)address,4096,3,0x32,-1,0)==(void *)address;
}
static void jump(unsigned int address,void *target) {
    unsigned int words[2]={0xe51ff004u,(unsigned int)target};
    memcpy((void *)address,words,sizeof words);
}
int probe_main(void) {
    if(!page(0x1cc000) || !page(0x169000) || !page(0x171000)) return 2;
    memcpy((void *)NX_WIFI_GATE_ADDRESS,nx_wifi_gate_stock,sizeof nx_wifi_gate_stock);
    jump(0x1696a0,getter); jump(0x171a98,notification);
    manual_lens_wifi_flush((void *)0x1cc000,(void *)0x1cd000);
    manual_lens_wifi_flush((void *)0x169000,(void *)0x16a000);
    manual_lens_wifi_flush((void *)0x171000,(void *)0x172000);
    if(mprotect((void *)0x1cc000,4096,5) ||
       mprotect((void *)0x169000,4096,5) ||
       mprotect((void *)0x171000,4096,5)) return 3;
    int failed=0;
    for(unsigned int variant=0;variant<2;variant++) {
        if(variant && (!patch_manual_lens_wifi() ||
                       manual_lens_wifi_result!=NX_MANUAL_LENS_WIFI_INSTALLED)) return 4;
        for(unsigned int i=0;i<sizeof cases/sizeof cases[0];i++) {
            const struct test_case *t=cases+i;
            next_mode=t->mode; next_state=t->state;
            last_error=0; getter_calls=0; bad_calls=0;
            int returned=invoke_tail((void *)0x1cccb0,t->connection,t->kmount,t->lock);
            int pass=returned==t->returned[variant] && last_error==t->error[variant] &&
                     getter_calls==t->calls[variant] && !bad_calls;
            printf("{\"case\":\"%s\",\"variant\":\"%s\",\"returned\":%d,"
                   "\"notification\":%d,\"getter_calls\":%d,\"pass\":%s}\n",
                   t->name,variant?"production_patch":"stock",returned,last_error,
                   getter_calls,pass?"true":"false");
            if(!pass) ++failed;
        }
    }
    if(fflush((void *)0)) return 11;
    return failed?10:0;
}
'''


def main():
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    evidence = write_wifi_stock_header(OUT)
    (OUT / 'wifi-gate-fixture.c').write_text(FIXTURE, encoding='ascii')
    subprocess.run([
        compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
        '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror',
        '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-pie',
        '-ffunction-sections', '-fdata-sections', '-I.', '-I' + str(OUT),
        '-c', str(OUT / 'wifi-gate-fixture.c'), '-o', str(OUT / 'wifi-gate-fixture.o'),
    ], cwd=PROJECT, check=True)
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
guest=/opt/nx300m-lab/stock-1.15
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-ld" -m armelf_linux_eabi --gc-sections -e _start \\
 --dynamic-linker /lib/ld-linux.so.3 -rpath-link "$guest/lib" \\
 outputs/controls/manual-lens/wifi-gate-fixture.o "$guest/lib/libc.so.6" \\
 -o outputs/controls/manual-lens/wifi-gate-fixture.arm
timeout 10 "$guest/qemu-arm-static" -L "$guest" outputs/controls/manual-lens/wifi-gate-fixture.arm
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-c', script],
                            capture_output=True, timeout=30)
    stdout = result.stdout.decode('utf-8', 'replace')
    rows = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
    passed = result.returncode == 0 and len(rows) == 16 and all(row['pass'] for row in rows)
    report = {
        'scope': 'stock A32 tail 0x1cccb0..0x1ccd8f in standalone QEMU with old stock ARM libc',
        'patch': 'production patch_manual_lens_wifi() from shared runtime header',
        'stock_function': evidence,
        'fixtures': ['connection/Kmount/Lenslock stack slots', 'UI_Get_Value continuation values',
                     'notification callback', 'process identity and opt-in for production installer'],
        'not_exercised': ['earlier storage/busy admission checks', 'real lens hardware',
                          'real camera process startup', 'RVF connection or detach events'],
        'case_count': len(rows), 'pass': passed, 'returncode': result.returncode,
        'cases': rows, 'stdout': stdout, 'stderr': result.stderr.decode('utf-8', 'replace'),
        'physical_rvf_verified': False,
    }
    (OUT / 'wifi-gate-probe.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(result.returncode or 1)


if __name__ == '__main__':
    main()
