"""Check LUT selection persistence on ARM in isolated RAM; no camera needed."""
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/metol/lut-selection-probe'
SOURCE = r'''
#define main nx_lut_cli_main
#include "mod/metol/lut_engine.c"
#undef main
static void check(int ok,const char *name) {
    if (!ok) {fprintf(stderr,"FAIL %s\n",name);exit(1);}
    printf("PASS %s\n",name);
}
static int selected(unsigned int expected) {
    unsigned int generation,strength;char path[256];
    return nx_lut_read_selection_with_strength(NX_LUT_SELECTION,&generation,path,&strength) &&
        generation && strength==expected && !strcmp(path,"/tmp/lut-selection/looks/test/table.d4lut");
}
int main(void) {
    setenv("NX300M_MOD_ROOT","/tmp/lut-selection",1);
    unsetenv("NX300M_MOD_SOURCE_ROOT");unsetenv("NX300M_MOD_SOURCE_ID");
    mkdir("/tmp/lut-selection",0700);mkdir("/tmp/lut-selection/looks",0700);
    mkdir("/tmp/lut-selection/looks/test",0700);
    FILE *out=fopen("/tmp/lut-selection/looks.txt","w");
    if (!out || fputs("test|Test|0|0|0|0|0|0|0\n",out)<0 || fclose(out)) return 2;
    out=fopen("/tmp/lut-selection/looks/test/table.d4lut","wb");uint16_t table[2190]={0};
    if (!out || fwrite(table,sizeof table,1,out)!=1 || fclose(out)) return 2;
    const char *path="/tmp/lut-selection/looks/test/table.d4lut";
    check(!nx_lut_stage_with_strength(path,50,NULL) && nx_lut_selection_persisted(),"save selected look and strength");
    unlink(NX_LUT_SELECTION);unlink(NX_LUT_SESSION);unlink(NX_LUT_COUNTER);
    check(!nx_lut_restore_selection() && selected(50),"restore after RAM state loss");
    char *begin[]={"engine","begin"},*off[]={"engine","off"};
    check(!action(2,begin) && !nx_lut_stage_with_strength(path,100,NULL) &&
        !action(2,off) && selected(50) && nx_lut_selection_persisted(),"panel cancel restores and saves previous choice");
    check(!nx_lut_stage_with_strength(path,0,NULL) && selected(0) && nx_lut_selection_persisted(),"zero strength is preserved");
    check(!nx_lut_stock() && nx_lut_selection_persisted(),"stock choice is saved explicitly");
    unlink(NX_LUT_SESSION);
    check(!nx_lut_restore_selection() && access(NX_LUT_SELECTION,F_OK),"stock stays stock after restart");
    check(!nx_lut_stage_with_strength(path,100,NULL),"prepare prior saved choice");
    mkdir("/tmp/lut-selection/.state/lut-selection.tmp",0700);
    check(!nx_lut_stage_with_strength(path,50,NULL) && selected(50) && !nx_lut_selection_persisted(),"card write failure keeps RAM choice and reports unsaved");
    check(!nx_lut_stock() && !nx_lut_selection_persisted() && !nx_lut_restore_selection() &&
        access(NX_LUT_SELECTION,F_OK),"failed stock save does not revive old look in this session");
    rmdir("/tmp/lut-selection/.state/lut-selection.tmp");unlink(NX_LUT_SESSION);
    out=fopen("/tmp/lut-selection/.state/lut-selection","w");
    if (!out || fprintf(out,"3 %s\n",path)<0 || fclose(out)) return 2;
    check(!nx_lut_restore_selection() && selected(100),"legacy saved format restores full strength");
    unlink(NX_LUT_SELECTION);unlink(NX_LUT_SESSION);
    unlink("/tmp/lut-selection/looks/test/table.d4lut");
    check(nx_lut_restore_selection()==5 && access(NX_LUT_SELECTION,F_OK),"missing table is not restored");
    return 0;
}
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'fixture.c').write_text(SOURCE, encoding='utf-8', newline='\n')
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = r'''set -eu
cd PROJECT_PATH
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
out=outputs/metol/lut-selection-probe
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. \
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,-rpath-link,"$rf/lib" "$out/fixture.c" mod/metol/remote/compat.c \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc -o "$out/fixture.arm"
unset LD_LIBRARY_PATH
exec unshare --mount --fork sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp
 mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs /run
 exec /opt/nx300m-lab/stock-1.15/qemu-arm-static -cpu cortex-a9 -L /opt/nx300m-lab/stock-1.15 "$1/outputs/metol/lut-selection-probe/fixture.arm"
' sh PROJECT_PATH
'''.replace('PROJECT_PATH', shlex.quote(base))
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    (OUT / 'result.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    (OUT / 'report.json').write_text(json.dumps({'passed': result.returncode == 0,
        'checks': result.stdout.count('PASS '), 'scope': 'ARM selection persistence, no camera or image verification'}, indent=2)+'\n', encoding='utf-8')
    print(result.stdout + result.stderr, end='')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
