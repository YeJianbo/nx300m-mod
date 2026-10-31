"""Build the ARM capture-AE shutter bound and optionally probe the stock getter."""

import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/controls"
GUEST = "/opt/nx300m-lab/stock-1.15"

PROBE = r'''
extern int printf(const char *, ...);
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern int stock_via_plt(void *) __asm__("_ZN9CAeInfoIF16calcTvMaxCaptureEv");
typedef int (*Getter)(void *);
typedef unsigned int (*Convert)(void *, int);
int main(void) {
    void *iq = dlopen("libiq-fw-slpcam.so", 2);
    if (!iq) return 2;
    Getter original = (Getter)dlsym(iq, "_ZN9CAeInfoIF16calcTvMaxCaptureEv");
    Getter hook = (Getter)dlsym((void *)0, "_ZN9CAeInfoIF16calcTvMaxCaptureEv");
    Convert convert = (Convert)dlsym(iq, "_ZN11CAeSensorIF23AeChangeTvalue256ToUsecEi");
    int (*configure)(unsigned int) = dlsym((void *)0, "nx_shutter_limit_configure");
    unsigned int (*selection)(void) = dlsym((void *)0, "nx_shutter_limit_selection");
    unsigned int fixture[0x67c/4] = {0};
    const int tv[] = {0,94,111,127,143,159,175,191,200,207};
    const unsigned int denominators[] = {0,60,125,250,500,1000,2000,4000,6000,8000};
    unsigned int rows = 0;
    if (!original || !hook || original == hook || !convert || !configure || !selection) return 3;
    if (selection() != 0) return 4;
    for (unsigned int choice = 0; choice < 10; choice++) {
        if (configure(choice)) return 5;
        unsigned int threshold = choice ? (1000000 + denominators[choice] - 1)/denominators[choice] : 0;
        if (choice && (convert((void *)0, tv[choice]<<4) < threshold ||
                       convert((void *)0, (tv[choice]+1)<<4) >= threshold)) return 6;
        for (unsigned int flag = 0; flag < 2; flag++) {
            *(short *)((char *)fixture + 0xc8) = (short)flag;
            int raw = original(fixture);
            int actual = stock_via_plt(fixture);
            int expected = choice && raw > tv[choice] ? tv[choice] : raw;
            if (raw != (flag ? 208 : 200) || actual != expected) return 7;
            unsigned int us = convert((void *)0, actual<<4);
            if (choice && us < threshold) return 8;
            printf("{\"event\":\"getter_probe\",\"selection\":%u,\"stock_flag\":%u,\"stock_tv16\":%d,\"result_tv16\":%d,\"result_us\":%u}\n", choice, flag, raw, actual, us);
            rows++;
        }
    }
    if (configure(10) != -102 || selection() != 9 || configure(0) || selection() != 0) return 9;
    for (unsigned int flag = 0; flag < 2; flag++) {
        *(short *)((char *)fixture + 0xc8) = (short)flag;
        if (stock_via_plt(fixture) != original(fixture)) return 10;
    }
    printf("{\"event\":\"shutter_limit_verified\",\"rows\":%u,\"stock_getter_forwarded\":true,\"stock_time_conversion_used\":true,\"off_restored\":true,\"invalid_rejected\":true,\"live_ae_solver_verified\":false}\n", rows);
    return 0;
}
'''

CRT = r'''
.syntax unified
.arm
.text
.globl _start
.type _start,%function
_start:
 mov fp,#0
 mov lr,#0
 ldr r1,[sp]
 add r2,sp,#4
 ldr r0,=main
 mov r3,#0
 mov r4,#0
 mov r5,#0
 mov r6,sp
 mov r7,#0
 push {r4,r5,r6,r7}
 bl __libc_start_main
 bl abort
.size _start,.-_start
.section .note.GNU-stack,"",%progbits
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("clang")
    if not compiler:
        raise SystemExit("clang is required")
    artifact = OUT / "shutter_limit.so"
    subprocess.run([
        compiler, "--target=arm-linux-gnueabi", "-march=armv7-a", "-mfloat-abi=soft",
        "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-ffreestanding",
        "-fno-builtin", "-fno-stack-protector", "-nostdlib", "-shared", "-fuse-ld=lld",
        "runtime/shutter_limit.c", "-o", str(artifact),
    ], cwd=ROOT, check=True)
    print(artifact)
    if not args.probe:
        return
    (OUT / "shutter-limit-probe.c").write_text(PROBE, encoding="utf-8")
    (OUT / "shutter-limit-crt0.S").write_text(CRT, encoding="utf-8")
    project = "/mnt/" + ROOT.drive[0].lower() + ROOT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(project)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 outputs/controls/shutter-limit-probe.c outputs/controls/shutter-limit-crt0.S \
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so -Wl,-l:libdiosal.so \
 -Wl,-l:libproduction-mode.so "$rf/usr/lib/libudd.so.3.1" \
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o outputs/controls/shutter-limit-probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
mount --make-rprivate /
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs {GUEST}/mod
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
cp outputs/controls/shutter-limit-probe.arm {GUEST}/mod/shutter-limit-probe.arm
cp outputs/controls/shutter_limit.so {GUEST}/mod/shutter_limit.so
chmod 755 {GUEST}/mod/shutter-limit-probe.arm {GUEST}/mod/shutter_limit.so
exec chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E LD_PRELOAD=/mod/shutter_limit.so -cpu cortex-a9 -L / /mod/shutter-limit-probe.arm'
'''
    result = subprocess.run(["wsl.exe", "-d", "Debian", "-u", "root", "-e", "sh", "-c", script], capture_output=True, timeout=45)
    stdout = result.stdout.decode("utf-8", "replace")
    stderr = result.stderr.decode("utf-8", "replace")
    (OUT / "shutter-limit-probe.stdout.log").write_text(stdout, encoding="utf-8")
    (OUT / "shutter-limit-probe.stderr.log").write_text(stderr, encoding="utf-8")
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{"event":')]
    report = {"exit_code": result.returncode, "stock_library":"unmodified NX300M 1.15 libiq-fw-slpcam.so",
              "input":"explicit zeroed CAeInfoIF-sized fixture with +0xc8 set to 0 or 1; no live camera object",
              "events":events, "shared_camera_session_changed":False,
              "physical_exposure_verified":False, "live_ae_solver_verified":False}
    (OUT / "shutter-limit-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(stdout)
    if result.returncode:
        print(stderr)
    print(json.dumps({"exit_code":result.returncode,"events":len(events)}))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
