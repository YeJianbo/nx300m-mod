"""Build the RGB bridge; probe original matrix math and genuine stock setters."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/metol'
GUEST = '/opt/nx300m-lab/stock-1.15'


def build_bridge():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / 'color-style.so'
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    # Source interpolation needs ARM soft-float/int64 support. Link only the
    # required libgcc routines privately; do not override the app's arithmetic.
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -march=armv7-a -mfloat-abi=soft -std=c99 -O2 -Wall -Wextra -Werror \\
 -fPIC -ffreestanding -fno-builtin -fno-stack-protector \\
 -ffp-contract=off -fno-fast-math -fexcess-precision=standard \\
 -nostdlib -shared -Wl,-z,nocombreloc -Wl,--exclude-libs,ALL \\
 runtime/color_style.c -lgcc -o outputs/metol/color-style.so
'''
    built = subprocess.run(['wsl.exe','-u','root','-e','sh','-c',script],capture_output=True,timeout=60)
    (OUTPUT/'color-style-build.log').write_bytes(built.stdout+built.stderr)
    if built.returncode:
        raise RuntimeError(f'Color bridge build exit {built.returncode}: {built.stderr.decode("utf-8","replace")}')
    return target


def run_lifecycle_probe():
    """Exercise process ownership in an isolated native preload fixture.

    ARM production compilation runs first. This probe tests fork/exec and file
    ownership, not native camera arithmetic or hardware restoration correctness.
    """
    fixture = OUTPUT / 'color-style-lifecycle-fixture.c'
    probe = OUTPUT / 'color-style-lifecycle-probe.c'
    fixture.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
static int *calls;
static int fork_inside, last_child;
static int (*callback)(void *);
static void *callback_data;
void ecore_main_loop_begin(void);
int lifecycle_tick(void);
void lifecycle_setup(void) { calls=mmap(0,4096,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0); }
int lifecycle_calls(void) { return *calls; }
void lifecycle_fork_next(void) { fork_inside=1; }
void lifecycle_fork_startup(void) { fork_inside=2; }
int lifecycle_child(void) { return last_child; }
int ipcs_color_correction_param(void *matrix) {
    (void)matrix; ++*calls;
    FILE *file=fopen("/var/run/lifecycle-setters.log","a");
    if(file) { fprintf(file,"%d\n",getpid()); fclose(file); }
    if(fork_inside) {
        int action=fork_inside; fork_inside=0; last_child=fork();
        if(!last_child) {
            alarm(3);
            if(action==2) { ecore_main_loop_begin(); if(lifecycle_tick()!=1) _exit(9); }
            exit(0);
        }
    }
    return 0;
}
int ipcm_color_correction_param(void *matrix) { return ipcs_color_correction_param(matrix); }
void *ecore_timer_add(double interval,int (*function)(void *),void *data) {
    (void)interval; callback=function; callback_data=data; return &callback;
}
void ecore_main_loop_begin(void) { }
int lifecycle_tick(void) { return callback?callback(callback_data):0; }
''', encoding='utf-8')
    probe.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
extern void lifecycle_setup(void), lifecycle_fork_next(void), lifecycle_fork_startup(void), ecore_main_loop_begin(void);
extern int lifecycle_calls(void), lifecycle_child(void), lifecycle_tick(void);
extern int ipcs_color_correction_param(void *);
static const char *stems[]={"color-style","source-rgb","chroma-style","sharpness","contrast","recipe"};
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"LIFECYCLE_FAIL line=%d\n",__LINE__); return 1; } } while(0)
static void path(char *out,const char *stem,const char *suffix) { snprintf(out,160,"/var/run/nx300m-%s-%s",stem,suffix); }
static int marker_pid(const char *stem) {
    char name[160]; path(name,stem,"ready"); FILE *file=fopen(name,"r"); int pid=0;
    if(file) { if(fscanf(file,"%d",&pid)!=1) pid=0; fclose(file); } return pid;
}
static int markers(int owner) {
    for(int i=0;i<6;i++) if(marker_pid(stems[i])!=owner) return 0;
    return 1;
}
static void seed(int owner) {
    for(int i=0;i<6;i++) {
        char name[160]; path(name,stems[i],"ready"); FILE *file=fopen(name,"w"); fprintf(file,"%d\n",owner); fclose(file);
        path(name,stems[i],"request"); file=fopen(name,"w"); fputs("preserve\n",file); fclose(file);
        path(name,stems[i],"response"); file=fopen(name,"w"); fputs("preserve\n",file); fclose(file);
    }
}
static int child_ok(int child) { int status=-1; return child>0 && waitpid(child,&status,0)==child && status==0; }
int main(int argc,char **argv) {
    if(argc==3 && !strcmp(argv[1],"--helper")) {
        CHECK(markers(atoi(argv[2]))); ecore_main_loop_begin();
        CHECK(lifecycle_tick()==1 && markers(atoi(argv[2]))); return 0;
    }
    lifecycle_setup();
    int matrix[9]={128,0,0,0,128,0,0,0,128};
    if(argc==2 && !strcmp(argv[1],"--startup-fork")) {
        /* Fork inside the original setter while the bridge lock is held,
         * before the parent attached its first Ecore loop. */
        lifecycle_fork_startup(); CHECK(!ipcs_color_correction_param(matrix));
        CHECK(child_ok(lifecycle_child()) && lifecycle_calls()==1);
        CHECK(!marker_pid("color-style"));
        puts("LIFECYCLE_PROBE pre_ecore_fork_adopted=pass inherited_lock_reset=pass inherited_cache_discarded=pass child_owner_cleanup=pass");
        return 0;
    }
    CHECK(!ipcs_color_correction_param(matrix));
    CHECK(lifecycle_calls()==1);
    ecore_main_loop_begin();
    CHECK(marker_pid("color-style")==getpid());
    seed(getpid());
    int child=fork();
    if(!child) {
        alarm(3);
        ecore_main_loop_begin();
        if(lifecycle_tick()!=0 || ipcs_color_correction_param(matrix)) _exit(7);
        exit(0);
    }
    CHECK(child_ok(child) && markers(getpid()) && lifecycle_calls()==2);
    lifecycle_fork_next();
    CHECK(!ipcs_color_correction_param(matrix));
    CHECK(child_ok(lifecycle_child()) && markers(getpid()) && lifecycle_calls()==3);
    child=fork();
    if(!child) {
        char pid[32]; snprintf(pid,sizeof pid,"%d",getppid());
        execl(argv[0],argv[0],"--helper",pid,(char *)0); _exit(8);
    }
    CHECK(child_ok(child) && markers(getpid()) && lifecycle_calls()==3);
    unlink("/var/run/nx300m-color-style-ready");
    CHECK(lifecycle_tick()==1 && marker_pid("color-style")==getpid());
    /* A replacement marker belongs to another live process; destructor must
     * preserve that channel while removing its own channels. */
    FILE *file=fopen("/var/run/nx300m-contrast-ready","w"); fprintf(file,"1\n"); fclose(file);
    puts("LIFECYCLE_PROBE fork_exit_parent_markers=pass inherited_locked_setter_exit=pass child_timer_stopped=pass child_setter_passthrough=pass exec_preload_parent_markers=pass ready_republished=pass");
    return 0;
}
''', encoding='utf-8')
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
cc -std=c99 -O2 -Wall -Wextra -Werror -fPIC -shared runtime/color_style.c -ldl -o outputs/metol/color-style-lifecycle-native.so
cc -D_GNU_SOURCE -std=c99 -O2 -Wall -Wextra -Werror -fPIC -shared outputs/metol/color-style-lifecycle-fixture.c -o outputs/metol/color-style-lifecycle-fixture.so
cc -std=c99 -O2 -Wall -Wextra -Werror outputs/metol/color-style-lifecycle-probe.c -Loutputs/metol -Wl,--no-as-needed -l:color-style-lifecycle-fixture.so -o outputs/metol/color-style-lifecycle-probe
exec unshare --mount --net --ipc sh -ec '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 timeout 15 env LD_LIBRARY_PATH="$1/outputs/metol" LD_PRELOAD="$1/outputs/metol/color-style-lifecycle-native.so" "$1/outputs/metol/color-style-lifecycle-probe"
 for stem in color-style source-rgb chroma-style sharpness recipe; do
  test ! -e "/var/run/nx300m-$stem-ready"
  test ! -e "/var/run/nx300m-$stem-request"
  test ! -e "/var/run/nx300m-$stem-response"
 done
 test "$(cat /var/run/nx300m-contrast-ready)" = 1
 test -e /var/run/nx300m-contrast-request
 test -e /var/run/nx300m-contrast-response
 test "$(wc -l </var/run/lifecycle-setters.log)" = 4
 echo "LIFECYCLE_PROBE owner_exit_cleanup=pass foreign_marker_preserved=pass fork_no_cached_restore=pass owner_cached_restore=pass"
 : > /var/run/lifecycle-setters.log
 timeout 15 env LD_LIBRARY_PATH="$1/outputs/metol" LD_PRELOAD="$1/outputs/metol/color-style-lifecycle-native.so" "$1/outputs/metol/color-style-lifecycle-probe" --startup-fork
 test ! -e /var/run/nx300m-color-style-ready
 test "$(wc -l </var/run/lifecycle-setters.log)" = 2

' sh {shlex.quote(base)}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', script],
                            capture_output=True, timeout=45)
    stdout = result.stdout.decode('utf-8', 'replace')
    stderr = result.stderr.decode('utf-8', 'replace')
    (OUTPUT / 'color-style-lifecycle.stdout.log').write_text(stdout, encoding='utf-8')
    (OUTPUT / 'color-style-lifecycle.stderr.log').write_text(stderr, encoding='utf-8')
    report = {'exit_code': result.returncode, 'production_arm_build': True,
              'fixture_architecture': 'native Linux fork/exec preload lifecycle only',
              'used_camera_session': False, 'image_or_arithmetic_verified': False,
              'pre_ecore_startup_fork_verified': result.returncode == 0 and 'pre_ecore_fork_adopted=pass' in stdout,
              'real_failure_evidence': 'outputs/metol/color-lifecycle/pid-ownership-before.json: host20183 namespace91, inherited constructor PID89, attached0',
              'stdout': stdout, 'stderr': stderr}
    (OUTPUT / 'color-style-lifecycle.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'生命周期探针退出码 {result.returncode}: {stderr.strip()}\n{stdout.strip()}')
    print(stdout, end='')
    return report


def run_original_probe():
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
base={shlex.quote(base)}
cd "$base"
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/color_style_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so \\
 -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so \\
 "$rf/usr/lib/libudd.so.3.1" \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o outputs/metol/color-style-probe.arm
"$tc/bin/arm-linux-gnueabi-nm" -D "$rf/usr/lib/libiq-fw-slpcam.so" > outputs/metol/color-style-symbols.txt
"$tc/bin/arm-linux-gnueabi-objdump" -dC --start-address=0x34a010 \\
 --stop-address=0x34a088 "$rf/usr/lib/libiq-fw-slpcam.so" \\
 > outputs/metol/color-style-matrix-math.asm
"$tc/bin/arm-linux-gnueabi-objdump" -dC --start-address=0x911f8 \\
 --stop-address=0x914b0 "$rf/usr/lib/libudd.so.3.1" \\
 > outputs/metol/color-style-ipcs-abi.asm
"$tc/bin/arm-linux-gnueabi-objdump" -dC --start-address=0x47aec \\
 --stop-address=0x47d94 "$rf/usr/lib/libudd.so.3.1" \\
 > outputs/metol/color-style-ipcm-abi.asm
unset LD_LIBRARY_PATH
"$rf/qemu-arm-static" -L "$rf" "$base/outputs/metol/color-style-probe.arm"
'''
    result = subprocess.run(
        ['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
        capture_output=True, timeout=90,
    )
    stdout = result.stdout.decode('utf-8', 'replace')
    stderr = result.stderr.decode('utf-8', 'replace')
    (OUTPUT / 'color-style-probe.stdout.log').write_text(stdout, encoding='utf-8')
    (OUTPUT / 'color-style-probe.stderr.log').write_text(stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(
            f'原厂矩阵探针退出码 {result.returncode}: {stderr.strip()}\n{stdout.strip()}'
        )
    if 'MATRIX_PROBE=PASS' not in stdout:
        raise RuntimeError(f'原厂矩阵探针没有 PASS 标记：{stdout.strip()}')
    return stdout


def run_bridge_probe():
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
base={shlex.quote(base)}
cd "$base"
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -shared -fPIC -nostdlib \\
 runtime/color_style_probe_ecore.c -o outputs/metol/color-style-probe-ecore.so
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/color_style_bridge_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -Wl,--no-as-needed outputs/metol/color-style-probe-ecore.so \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o outputs/metol/color-style-bridge-probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 exec "$1/qemu-arm-static" -L "$1" \\
  -E LD_LIBRARY_PATH="$2:$1/usr/lib:$1/lib" \\
  -E LD_PRELOAD="$2/outputs/metol/color-style.so" \\
  "$2/outputs/metol/color-style-bridge-probe.arm"
' sh "$rf" "$base"
'''
    result = subprocess.run(
        ['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
        capture_output=True, timeout=60,
    )
    stdout = result.stdout.decode('utf-8', 'replace')
    stderr = result.stderr.decode('utf-8', 'replace')
    (OUTPUT / 'color-style-bridge-probe.stdout.log').write_text(stdout, encoding='utf-8')
    (OUTPUT / 'color-style-bridge-probe.stderr.log').write_text(stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(
            f'RGB 桥原厂 setter 探针退出码 {result.returncode}: {stderr.strip()}\n{stdout.strip()}'
        )
    if 'BRIDGE_PROBE=PASS' not in stdout:
        raise RuntimeError(f'RGB 桥探针没有 PASS 标记：{stdout.strip()}')
    return stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--lifecycle-only', action='store_true')
    args = parser.parse_args()
    bridge = build_bridge()
    if args.lifecycle_only:
        run_lifecycle_probe()
        return
    if args.build_only:
        print(bridge)
        return
    probe_stdout = run_original_probe()
    bridge_stdout = run_bridge_probe()
    report = {
        'firmware': 'Samsung NX300M 1.15',
        'bridge': str(bridge.relative_to(PROJECT)).replace('\\', '/'),
        'bridge_scope': 'LD_PRELOAD adapter for original IPCS/IPCM setters',
        'original_libraries': [
            'unmodified usr/lib/libiq-fw-slpcam.so',
            'unmodified usr/lib/libudd.so.3.1',
        ],
        'matrix': {
            'abi': 'nine 32-bit unsigned words col_mat[0..8]',
            'layout': 'row-major, from original CIpcMath::MatrixMult3x3 loop order',
            'q_format': 'Native CCM coefficients; encoded base row sums are128, while PSS/ColorTone multiplication uses divisor256',
            'multiply': 'A*B; signed division truncates toward zero',
            'gain_definition': 'M_prime=diag(R,G,B)*M; percentages scale output rows',
            'setter_range': [-1024, 1023],
            'range_policy': 'forward the original matrix unchanged if any styled coefficient is outside the original setter range',
        },
        'probe': {
            'exit_code': 0,
            'stdout': probe_stdout.strip().splitlines(),
            'used_sensor_or_camera_session': False,
            'stock_libraries_modified': False,
            'image_verified': False,
        },
        'bridge_probe': {
            'exit_code': 0,
            'stdout': bridge_stdout.strip().splitlines(),
            'bridge': 'the same production outputs/metol/color-style.so',
            'setters': 'unmodified NX300M 1.15 libudd.so.3.1 IPCS and IPCM',
            'register_backend': 'separately allocated synthetic RAM; full byte comparison',
            'ecore_scheduler': 'fixture stub captures and explicitly invokes production request callback',
            'checks': [
                'neutral passthrough', 'independent RGB row gains',
                'repeated stock matrix does not accumulate', 'fresh stock base',
                'positive and negative overflow bypass the whole matrix',
                'reset restores latest stock base', 'neutral after reset',
            ],
            'used_sensor_or_camera_session': False,
            'stock_libraries_modified': False,
            'image_verified': False,
        },
        'integration': {
            'camera_session_loaded': False,
            'ecore_request_and_ready_probe': False,
            'image_verified': False,
        },
    }
    (OUTPUT / 'color-style-probe.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
