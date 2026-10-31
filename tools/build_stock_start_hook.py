"""Build a narrowly patched stock libmisc and optionally probe it in a private root.

Writes only outputs/deployment/start-hook. Never replaces the running rootfs or
creates an SLP firmware package.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import shlex
import struct
import subprocess
import sys
import tarfile

os.environ.setdefault("CONDA_PREFIX", sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/start-hook"
ENTRY = 0x1218
EXPECTED_ENTRY = bytes.fromhex("0000a0e31eff2fe1")
COMMAND = b"test -f /mnt/mmc/nx300m-mod/start.sh && /bin/sh /mnt/mmc/nx300m-mod/start.sh >/dev/null 2>&1 &\0"

PROBE_C = r'''
extern int printf(const char *, ...);
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern const char *dlerror(void);
extern int open(const char *, int, ...);
extern int read(int, void *, unsigned int);
extern int write(int, const void *, unsigned int);
extern int close(int);
extern int access(const char *, int);
extern int usleep(unsigned int);
extern int probe_abi(void *);
int main(void) {
    void *library = dlopen("/usr/lib/libmisc.so", 2);
    if (!library) { printf("dlopen: %s\n", dlerror()); return 2; }
    void *entry = dlsym(library, "start_script");
    if (!entry) return 3;
    const char *script = "/mnt/mmc/nx300m-mod/start.sh";
    const char *marker = "/mnt/mmc/nx300m-mod/dispatched";
    if (!access(script, 0) || !access(marker, 0)) return 4;
    int result = probe_abi(entry);
    usleep(200000);
    if (result || !access(script, 0) || !access(marker, 0)) return 5;
    printf("{\"event\":\"empty_sd\",\"abi_result\":0,\"script_created\":false,\"marker_created\":false}\n");
    const char body[] = "#!/bin/sh\nprintf 'fixture-dispatched\\n' >> /mnt/mmc/nx300m-mod/dispatched\n";
    int fd = open(script, 1|64|512, 0644);
    if (fd < 0 || write(fd, body, sizeof(body)-1) != sizeof(body)-1 || close(fd)) return 6;
    result = probe_abi(entry);
    if (result) return 7;
    for (int i=0; i<100 && access(marker, 0); i++) usleep(20000);
    usleep(100000);
    fd = open(marker, 0);
    if (fd < 0) return 8;
    char buffer[128] = {0};
    int count = read(fd, buffer, sizeof(buffer)-1);
    close(fd);
    int lines = 0;
    for (int i=0; i<count; i++) if (buffer[i] == '\n') lines++;
    if (count != 19 || lines != 1) { printf("marker bytes=%d lines=%d\n", count, lines); return 9; }
    printf("{\"event\":\"fixture_dispatched\",\"abi_result\":0,\"marker_lines\":1,\"marker_bytes\":19}\n");
    return 0;
}
'''

PROBE_ASM = r'''
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
.globl probe_abi
.type probe_abi,%function
probe_abi:
 push {r4-r11,lr}
 sub sp,sp,#4
 mov ip,r0
 mov r4,#0x44
 mov r5,#0x55
 mov r6,#0x66
 mov r7,#0x77
 mov r8,#0x88
 mov r9,#0x99
 mov r10,#0xaa
 mov r11,#0xbb
 blx ip
 cmp r4,#0x44
 orrne r0,r0,#1
 cmp r5,#0x55
 orrne r0,r0,#2
 cmp r6,#0x66
 orrne r0,r0,#4
 cmp r7,#0x77
 orrne r0,r0,#8
 cmp r8,#0x88
 orrne r0,r0,#16
 cmp r9,#0x99
 orrne r0,r0,#32
 cmp r10,#0xaa
 orrne r0,r0,#64
 cmp r11,#0xbb
 orrne r0,r0,#128
 mov r1,sp
 tst r1,#7
 orrne r0,r0,#256
 add sp,sp,#4
 pop {r4-r11,pc}
.size probe_abi,.-probe_abi
.section .note.GNU-stack,"",%progbits
'''


def wsl_path(path):
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


def elf_symbols(raw):
    elf = ELFFile(io.BytesIO(raw))
    return [(s.name, s["st_value"], s["st_size"], s["st_info"]["type"], s["st_info"]["bind"], s["st_other"]["visibility"])
            for s in elf.get_section_by_name(".dynsym").iter_symbols()]


def check_segments(elf):
    loads = sorted((s["p_vaddr"], s["p_vaddr"] + s["p_memsz"]) for s in elf.iter_segments() if s["p_type"] == "PT_LOAD")
    if any(a[1] > b[0] for a, b in zip(loads, loads[1:])):
        raise RuntimeError("Overlapping original or patched PT_LOAD virtual ranges")


def branch(source, target, link=False):
    delta = target-source-8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise RuntimeError("ARM branch out of range")
    return (0xeb000000 if link else 0xea000000) | ((delta//4) & 0xffffff)


def build():
    source = json.loads((ROOT / "work/stock-1.15/source.json").read_text(encoding="utf-8"))
    if (source["model"], source["version"], source["zip_entry_crc_verified_on_complete_read"]) != ("NX300M", "1.15", True):
        raise RuntimeError("Unverified stock provenance")
    with (ROOT / "work/stock-1.15/nx300m.bin").open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != source["local_binary_sha256"]:
            raise RuntimeError("Stock firmware identity changed")
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as archive:
        original = archive.extractfile("usr/lib/libmisc.so").read()
    elf = ELFFile(io.BytesIO(original))
    if elf["e_machine"] != "EM_ARM" or elf.elfclass != 32 or not elf.little_endian:
        raise RuntimeError("Unexpected target ELF")
    check_segments(elf)
    symbol = next(s for s in elf.get_section_by_name(".dynsym").iter_symbols() if s.name == "start_script")
    if (symbol["st_value"], symbol["st_size"], original[ENTRY:ENTRY+8]) != (ENTRY, 8, EXPECTED_ENTRY):
        raise RuntimeError("start_script does not match the reviewed original bytes")
    rx_index, rx = next((i, s) for i, s in enumerate(elf.iter_segments()) if s["p_type"] == "PT_LOAD" and s["p_flags"] == 5)
    if rx["p_offset"] or rx["p_vaddr"] or rx["p_filesz"] != rx["p_memsz"]:
        raise RuntimeError("Unexpected original RX mapping")
    dynamic = list(elf.get_section_by_name(".dynsym").iter_symbols())
    relocations = list(elf.get_section_by_name(".rel.plt").iter_relocations())
    system_index = next(i for i, r in enumerate(relocations) if dynamic[r["r_info_sym"]].name == "system")
    system_plt = elf.get_section_by_name(".plt")["sh_addr"] + 20 + 12*system_index
    append = (len(original) + 15) & ~15
    stub = struct.pack("<5I", 0xe92d4010, 0xe28f0008, branch(append+8, system_plt, True), 0xe3a00000, 0xe8bd8010)
    patched = bytearray(original)
    patched.extend(b"\0"*(append-len(original)) + stub + COMMAND)
    phoff = elf["e_phoff"] + rx_index*elf["e_phentsize"]
    fields = (phoff+16, phoff+20)
    struct.pack_into("<I", patched, ENTRY, branch(ENTRY, append))
    for offset in fields:
        struct.pack_into("<I", patched, offset, len(patched))
    decoded = list(Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(bytes(patched[ENTRY:ENTRY+4]), ENTRY))
    if len(decoded) != 1 or decoded[0].mnemonic != "b" or decoded[0].op_str != f"#0x{append:x}":
        raise RuntimeError("Entry branch failed static decoding")
    patched_elf = ELFFile(io.BytesIO(patched))
    check_segments(patched_elf)
    if elf_symbols(original) != elf_symbols(patched):
        raise RuntimeError("Export/import symbol table changed")
    allowed = {i for begin, length in ((ENTRY, 4), *((f, 4) for f in fields)) for i in range(begin, begin+length)}
    differences = [i for i in range(len(original)) if original[i] != patched[i]]
    if not set(differences) <= allowed or patched[ENTRY+4:ENTRY+8] != EXPECTED_ENTRY[4:]:
        raise RuntimeError("Changes outside the reviewed entry/header fields")
    ranges = []
    for offset in differences:
        if not ranges or offset != ranges[-1][1]:
            ranges.append([offset, offset+1])
        else:
            ranges[-1][1] += 1
    restored = bytearray(patched[:len(original)])
    for offset in allowed:
        restored[offset] = original[offset]
    if restored != original:
        raise RuntimeError("Restoration failed")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "libmisc.stock.so").write_bytes(original)
    (OUT / "libmisc.so").write_bytes(patched)
    report = {"source": "work/stock-1.15/rootfs.tar:usr/lib/libmisc.so", "stock_firmware_sha256": source["local_binary_sha256"],
              "original_size": len(original), "patched_size": len(patched), "original_member_sha256": hashlib.sha256(original).hexdigest(),
              "patched_member_sha256": hashlib.sha256(patched).hexdigest(), "original_entry_hex": EXPECTED_ENTRY.hex(),
              "patched_entry_hex": patched[ENTRY:ENTRY+8].hex(), "existing_byte_diff_ranges_end_exclusive": ranges,
              "appended_range_end_exclusive": [len(original), len(patched)], "RX_length_field_offsets": list(fields),
              "stub_address": append, "stub_disassembly": [f"{i.address:#x} {i.mnemonic} {i.op_str}" for i in Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(stub, append)],
              "command": COMMAND[:-1].decode(), "all_other_original_bytes_preserved": True, "all_dynamic_symbols_preserved": True,
              "PT_LOAD_virtual_ranges_overlap": False, "restore_in_memory_matches_stock": True,
              "restoration": "Use the retained libmisc.stock.so, or truncate to original_size and restore original 4 bytes at entry and 4-byte values at both RX length fields from that retained stock file.",
              "firmware_package_created": False, "running_rootfs_changed": False}
    (OUT / "build-report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


def stage_runtime():
    stage = OUT / "runtime-files"
    stage.mkdir(exist_ok=True)
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as archive:
        names = {m.name: m for m in archive.getmembers()}
        def resolve(name):
            for _ in range(20):
                member = names[name]
                if member.isfile():
                    return name, archive.extractfile(member).read()
                if member.issym():
                    name = posixpath.normpath(member.linkname.lstrip("/") if member.linkname.startswith("/") else posixpath.join(posixpath.dirname(name), member.linkname))
                elif member.islnk():
                    name = member.linkname.lstrip("/")
                else:
                    raise RuntimeError("Unexpected library member type")
            raise RuntimeError("Symlink cycle")
        pending = [("lib/ld-linux.so.3", "lib/ld-linux.so.3"), ("lib/libdl.so.2", "lib/libdl.so.2"), ("bin/arm/sh", "bin/busybox"), ("usr/lib/libmisc.so", "usr/lib/libmisc.so")]
        seen, provenance = set(), []
        while pending:
            destination, member_name = pending.pop()
            if destination in seen:
                continue
            seen.add(destination)
            origin, data = resolve(member_name)
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((OUT / "libmisc.so").read_bytes() if destination == "usr/lib/libmisc.so" else data)
            provenance.append({"destination": destination, "stock_member": origin, "patched": destination == "usr/lib/libmisc.so"})
            target_elf = ELFFile(io.BytesIO(data))
            dynamic = target_elf.get_section_by_name(".dynamic")
            if dynamic:
                for tag in dynamic.iter_tags():
                    if tag.entry.d_tag == "DT_NEEDED":
                        candidate = next((folder+tag.needed for folder in ("lib/", "usr/lib/") if folder+tag.needed in names), None)
                        if not candidate:
                            raise RuntimeError("Missing stock dependency: "+tag.needed)
                        pending.append((candidate, candidate))
    (OUT / "runtime-provenance.json").write_text(json.dumps(provenance, indent=2)+"\n", encoding="utf-8")
    return stage


def probe():
    stage = stage_runtime()
    (OUT / "probe.c").write_text(PROBE_C, encoding="utf-8")
    (OUT / "probe.S").write_text(PROBE_ASM, encoding="utf-8")
    probe_root = OUT / "probe-root"
    probe_root.mkdir(exist_ok=True)
    project = wsl_path(ROOT)
    output = wsl_path(OUT)
    root = wsl_path(probe_root)
    script = f'''set -eu
cd {shlex.quote(project)}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,{shlex.quote(wsl_path(stage / "lib"))} \\
 outputs/deployment/start-hook/probe.c outputs/deployment/start-hook/probe.S \\
 {shlex.quote(wsl_path(stage / "lib/libc.so.6"))} {shlex.quote(wsl_path(stage / "lib/libdl.so.2"))} \\
 -lgcc -o outputs/deployment/start-hook/probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
set -eu
mount --make-rprivate /
root={shlex.quote(root)}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(wsl_path(stage))}/. "$root"/
mkdir -p "$root/mnt/mmc/nx300m-mod" "$root/dev" "$root/tmp"
chmod -R a+rX "$root"
chmod 777 "$root/mnt/mmc/nx300m-mod" "$root/tmp"
cp outputs/deployment/start-hook/probe.arm "$root/probe.arm"
cp work/qemu/qemu-arm-static "$root/qemu-arm-static"
chmod 755 "$root/qemu-arm-static" "$root/probe.arm" "$root/bin/arm/sh"
printf "#!/qemu-arm-static /bin/arm/sh\\nexec /qemu-arm-static /bin/arm/sh \\"\\$@\\"\\n" > "$root/bin/sh"
chmod 755 "$root/bin/sh"
touch "$root/dev/null"
mount --bind /dev/null "$root/dev/null"
exec chroot --userspec=65534:65534 "$root" /qemu-arm-static -strace -cpu cortex-a9 -L / /probe.arm'
'''
    (OUT / "probe-host.sh").write_text(script, encoding="utf-8")
    result = subprocess.run(["wsl.exe", "-d", "Debian", "-u", "root", "-e", "sh", "-c", script], capture_output=True, timeout=45)
    stdout, stderr = (s.decode("utf-8", "replace") for s in (result.stdout, result.stderr))
    (OUT / "probe.stdout.log").write_text(stdout, encoding="utf-8")
    (OUT / "probe.stderr.log").write_text(stderr, encoding="utf-8")
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{"event":')]
    report = {"exit_code": result.returncode, "events": events, "stock_loader_and_libc": True,
              "system_interposed": False, "execve_bin_sh_observed": 'execve("/bin/sh"' in stderr,
              "shell_environment": "Explicit /bin/sh script bridge invokes the retained stock ARM busybox sh through qemu-arm-static; no global binfmt registration.",
              "isolation": "Fresh tmpfs root in private mount/network/IPC namespaces, uid/gid 65534, no camera devices, only /dev/null bound.",
              "stock_runtime_root_changed": False, "physical_camera_verified": False}
    (OUT / "probe-report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(stdout)
    if result.returncode:
        print(stderr[-8000:])
    print(json.dumps(report, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(), indent=2))
    if args.probe:
        probe()


if __name__ == "__main__":
    main()
