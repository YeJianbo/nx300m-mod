"""Run selected stock ARM production parser/handler bytes with recording-only stubs.

No camera access, USB requests, shell execution, or running stock rootfs changes.
The private probe root contains only a harness, original loader/libc, and input blobs.
"""
import io
import json
import os
from pathlib import Path
import posixpath
import shlex
import struct
import subprocess
import tarfile

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/production-probe"
LIB_BASE = 0x01000000
TRAMP = 0x00800000

C_SOURCE = r'''
typedef unsigned int u32;
extern int printf(const char *, ...), puts(const char *), strcmp(const char *, const char *);
extern unsigned int strlen(const char *);
extern void *memcpy(void *, const void *, unsigned int), *memset(void *, int, unsigned int);
extern char *strchr(const char *, int), *strtok(char *, const char *);
extern int snprintf(char *, unsigned int, const char *, ...);
extern void *mmap(void *, unsigned int, int, int, int, int);
extern int mprotect(void *, unsigned int, int), open(const char *, int, ...), close(int);
extern int read(int, void *, unsigned int);
extern void abort(void);
static int system_calls, flag_calls, name_calls;
static char captured[256], captured_name[64];
static int dummy;
static int no_call(void) { puts("UNEXPECTED_EXTERNAL_CALL"); abort(); return -1; }
static int record_system(const char *s) {
    system_calls++; snprintf(captured, sizeof captured, "%s", s);
    printf("{\"event\":\"system_recorded\",\"command\":\"%s\",\"executed\":false}\n", s);
    return 0;
}
static void *get_instance(void) { return &dummy; }
static void set_flag(void *self, int flag) {
    (void)self; flag_calls++;
    printf("{\"event\":\"mode_flag_recorded\",\"value\":%d}\n", flag);
}
static void set_name(void *self, const char **value) {
    (void)self; name_calls++; snprintf(captured_name, sizeof captured_name, "%s", *value);
    printf("{\"event\":\"filename_recorded\",\"value\":\"%s\"}\n", *value);
}
static void empty(void *p) { (void)p; }
static void string_ctor(const char **out, const char *text, void *allocator) {
    (void)allocator; *out = text;
}
static int quiet_log(void) { return 0; }
static void *callbacks[] = {
    (void *)memset, (void *)strlen, (void *)memcpy, (void *)strchr,
    (void *)strtok, (void *)strcmp, (void *)snprintf, (void *)record_system,
    (void *)get_instance, (void *)set_flag, (void *)set_name,
    (void *)empty, (void *)string_ctor, (void *)quiet_log, (void *)no_call
};
static int load_blob(const char *path, u32 address, u32 bytes) {
    u32 page = address & ~4095u, size = ((address + bytes + 4095u) & ~4095u) - page;
    if (mmap((void *)page, size, 3, 0x32, -1, 0) != (void *)page) return 1;
    int fd = open(path, 0); if (fd < 0) return 2;
    u32 got = 0;
    while (got < bytes) {
        int n = read(fd, (void *)(address + got), bytes - got);
        if (n <= 0) return 3;
        got += (u32)n;
    }
    close(fd);
    __builtin___clear_cache((char *)page, (char *)(page + size));
    return mprotect((void *)page, size, 5) ? 4 : 0;
}
static int check(int condition, int line) {
    if (!condition) printf("CHECK_FAILED:%d\n", line);
    return !condition;
}
#define CHECK(c) do { if (check((c), __LINE__)) return 1; } while (0)
int main(void) {
    u32 *relay = mmap((void *)0x00800000, 4096, 3, 0x32, -1, 0);
    CHECK(relay == (void *)0x00800000);
    for (u32 i=0; i<sizeof(callbacks)/sizeof(callbacks[0]); i++) {
        relay[i*4] = 0xe51ff004; relay[i*4+1] = (u32)callbacks[i];
    }
    __builtin___clear_cache((char *)relay, (char *)relay+4096);
    CHECK(!mprotect(relay, 4096, 5));
    CHECK(!load_blob("/app.blob", APP_START, APP_BYTES));
    CHECK(!load_blob("/library.blob", LIB_START, LIB_BYTES));
    void (*parse)(void *, void *, const char *) = (void *)0x01018598;
    int (*dispatch)(void *, void *) = (void *)0x0012bfd4;
    unsigned char command[0x240], parser[0x280];
    memset(parser, 0, sizeof parser);
    /* Literal sentinel only: the trailing colon is never passed to a shell. */
    const char *line = "line_process create_file on_mot NX_PROBE_SENTINEL;: ";
    parse(command, parser, line);
    CHECK(!strcmp((char *)command, "line_process"));
    CHECK(!strcmp((char *)command+0x32, "create_file"));
    CHECK(!strcmp((char *)command+0x64, "on_mot"));
    CHECK(!strcmp((char *)command+0x96, "NX_PROBE_SENTINEL;:"));
    printf("{\"event\":\"stock_parser\",\"semicolon_retained\":true,\"token_bytes\":%u}\n", strlen((char *)command+0x96));
    CHECK(dispatch(&dummy, command) == 1);
    CHECK(system_calls == 1 && flag_calls == 1 && name_calls == 1);
    CHECK(!strcmp(captured, "rm -rf /mnt/mmc/NX_PROBE_SENTINEL;:"));
    CHECK(!strcmp(captured_name, "NX_PROBE_SENTINEL;:"));
    parse(command, parser, "line_process create_file off ");
    CHECK(dispatch(&dummy, command) == 1);
    CHECK(system_calls == 1 && flag_calls == 2 && name_calls == 1);
    puts("{\"event\":\"probe_complete\",\"shell_executed\":false,\"hardware_calls\":0}");
    return 0;
}
'''

START_ASM = r'''
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
.section .note.GNU-stack,"",%progbits
'''


def wsl(path):
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


def branch(source, target):
    delta = target - source - 8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError("ARM branch outside range")
    return 0xeb000000 | ((delta // 4) & 0xffffff)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stage = OUT / "runtime-files"
    stage.mkdir(exist_ok=True)
    patches = []
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as archive:
        members = {m.name: m for m in archive.getmembers()}

        def resolve(name):
            for _ in range(20):
                member = members[name]
                if member.isfile():
                    return name, archive.extractfile(member).read()
                name = posixpath.normpath(member.linkname.lstrip("/") if member.linkname.startswith("/") else posixpath.join(posixpath.dirname(name), member.linkname))
            raise ValueError("Stock symlink cycle")

        provenance = []
        for target in ["lib/ld-linux.so.3", "lib/libc.so.6"]:
            source, raw = resolve(target)
            file = stage / target
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(raw)
            provenance.append({"destination": target, "stock_member": source})

        def prepare(member, load_base, functions, aliases, output):
            raw = archive.extractfile(member).read()
            elf = ELFFile(io.BytesIO(raw))
            segment = next(s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD")
            begin = max(segment["p_vaddr"], 0x10000 if load_base == 0 else 0)
            offset = segment["p_offset"] + begin - segment["p_vaddr"]
            end = segment["p_offset"] + segment["p_filesz"]
            blob = bytearray(raw[offset:end])
            syms = list(elf.get_section_by_name(".dynsym").iter_symbols())
            plt = elf.get_section_by_name(".plt")
            imports = {plt["sh_addr"] + 20 + i * 12: syms[r["r_info_sym"]].name for i, r in enumerate(elf.get_section_by_name(".rel.plt").iter_relocations())}
            callbacks = {"memset": 0, "strlen": 1, "memcpy": 2, "strchr": 3,
                         "strtok": 4, "strcmp": 5, "snprintf": 6, "system": 7,
                         "_ZN15CProductionMode11GetInstanceEv": 8,
                         "_ZNSaIcEC1Ev": 11, "_ZNSaIcED1Ev": 11,
                         "_ZNSsD1Ev": 11, "_ZNSsC1EPKcRKSaIcE": 12,
                         "slpcam_print_log": 13}
            for start, size in functions:
                for address in range(start, start + size, 4):
                    at = address - begin
                    word = struct.unpack_from("<I", blob, at)[0]
                    if word & 0x0f000000 != 0x0b000000:
                        continue
                    displacement = word & 0xffffff
                    if displacement & 0x800000:
                        displacement -= 0x1000000
                    target = address + 8 + 4 * displacement
                    name = imports.get(target, hex(target))
                    if target in aliases:
                        dest, label = aliases[target]
                    else:
                        index = callbacks.get(name, 14)
                        dest, label = TRAMP + index * 16, "callback:" + name
                    struct.pack_into("<I", blob, at, branch(address + load_base, dest))
                    patches.append({"source": member, "call": hex(address), "original_target": hex(target), "name": name, "replacement": label, "replacement_address": hex(dest)})
            (stage / output).write_bytes(blob)
            provenance.append({"destination": output, "stock_member": member,
                               "load_address": hex(begin + load_base), "bytes": len(blob),
                               "changes": "Selected external BL targets only; no stock file changed"})
            return begin + load_base, len(blob)

        app_start, app_bytes = prepare("usr/bin/di-camera-app-nx300", 0,
            [(0x12bfd4, 1804), (0x1397b0, 520)],
            {0x1397b0: (0x1397b0, "stock:create_file"),
             0x13f82c: (TRAMP + 9 * 16, "record:mode_flag"),
             0x13f85c: (TRAMP + 10 * 16, "record:filename")}, "app.blob")
        lib_start, lib_bytes = prepare("usr/lib/libproduction-mode.so", LIB_BASE,
            [(0x16700, 124), (0x18598, 240), (0x18c38, 644)],
            {0x1235c: (LIB_BASE + 0x16700, "stock:command_constructor"),
             0x121dc: (LIB_BASE + 0x18c38, "stock:getParam")}, "library.blob")

    source = f"#define APP_START {app_start}u\n#define APP_BYTES {app_bytes}u\n#define LIB_START {lib_start}u\n#define LIB_BYTES {lib_bytes}u\n" + C_SOURCE
    (OUT / "probe.c").write_text(source, encoding="utf-8")
    (OUT / "probe.S").write_text(START_ASM, encoding="utf-8")
    root = OUT / "probe-root"
    root.mkdir(exist_ok=True)
    script = f'''set -eu
cd {shlex.quote(wsl(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,-Ttext-segment=0x10000000 \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 {shlex.quote(wsl(OUT / 'probe.c'))} {shlex.quote(wsl(OUT / 'probe.S'))} \\
 {shlex.quote(wsl(stage / 'lib/libc.so.6'))} -lgcc -o {shlex.quote(wsl(OUT / 'probe.arm'))}
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
set -eu
mount --make-rprivate /
root={shlex.quote(wsl(root))}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(wsl(stage))}/. "$root"/
cp {shlex.quote(wsl(OUT / 'probe.arm'))} "$root/probe.arm"
cp work/qemu/qemu-arm-static "$root/qemu-arm-static"
chmod -R a+rX "$root"
chmod 755 "$root/probe.arm" "$root/qemu-arm-static"
exec chroot --userspec=65534:65534 "$root" /qemu-arm-static -strace -cpu cortex-a9 -L / /probe.arm'
'''
    (OUT / "probe-host.sh").write_text(script, encoding="utf-8")
    result = subprocess.run(["wsl.exe", "-d", "Debian", "-u", "root", "-e", "sh", "-c", script], capture_output=True, timeout=60)
    stdout, stderr = [value.decode("utf-8", "replace") for value in (result.stdout, result.stderr)]
    (OUT / "probe.stdout.log").write_text(stdout, encoding="utf-8")
    (OUT / "probe.stderr.log").write_text(stderr, encoding="utf-8")
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{"event":')]
    report = {"exit_code": result.returncode, "events": events, "source": "Verified original NX300M 1.15 rootfs.tar members",
              "executed_stock_code": ["_tPM_Command constructor", "CProductionScript::Parsing(char*)", "CProductionScript::getParam", "CProductionCommandDD::ExecuteCommand", "CProductionCommandDD::command_create_file"],
              "external_call_replacements": patches, "runtime_provenance": provenance,
              "shell_executed": False, "shell_available_in_probe_root": False,
              "isolation": "Fresh tmpfs chroot; private mount/network/IPC namespaces; uid/gid 65534; no camera devices or hardware services",
              "physical_camera_accessed": False, "usb_requests_sent": False,
              "real_usb_and_production_mode_gates_verified": False}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(stdout)
    if result.returncode:
        print(stderr[-7000:])
    print(json.dumps({"exit_code": result.returncode, "report": str(OUT / "report.json"), "events": len(events)}))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
