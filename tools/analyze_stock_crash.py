"""Resolve a stock ARM core dump against QEMU's actual library mappings."""

import json
import argparse
import re
import struct
import subprocess
from pathlib import Path, PurePosixPath

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path("//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15")
OUTPUT = PROJECT / "outputs/firmware-runtime"


def resolve_guest(path):
    for _ in range(20):
        target = ROOT / path.lstrip("/")
        if target.is_file(): return target
        resolved = subprocess.run(["wsl.exe", "-u", "root", "-e", "readlink", "/opt/nx300m-lab/stock-1.15/" + path.lstrip("/")], capture_output=True, text=True, check=True)
        link = resolved.stdout.strip()
        path = link if link.startswith("/") else (PurePosixPath(path).parent / link).as_posix()
    raise RuntimeError("Symlink loop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="camera-xvfb")
    parser.add_argument("--core", type=Path)
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--output-prefix", default="camera")
    args = parser.parse_args()
    run = json.loads((OUTPUT / "runs.json").read_text(encoding="utf-8"))["runs"][args.run]
    cores = run.get("core_files", [])
    core = args.core or (Path(cores[-1]) if cores else None)
    if core is None: parser.error("Specify --core or rerun the selected configuration with --core")
    with core.open("rb") as stream:
        elf = ELFFile(stream)
        status = next(note["n_desc"] for segment in elf.iter_segments() if segment["p_type"] == "PT_NOTE" for note in segment.iter_notes() if note["n_type"] == "NT_PRSTATUS")
        registers = struct.unpack_from("<18I", status, 72)
    trace = (OUTPUT / f"{args.run}.strace.log").read_text(encoding="utf-8")
    descriptors, mappings = {}, []
    for line in trace.splitlines():
        opened = re.search(r'\bopen\("([^"]+)".*\) = (\d+)$', line)
        if opened: descriptors[int(opened[2])] = opened[1]
        mapped = re.search(r"mmap2\(NULL,(\d+),([^)]*)\) = (0x[0-9a-f]+)$", line)
        if mapped and "PROT_EXEC" in mapped[2]:
            flags, fd, offset = mapped[2].rsplit(",", 2)
            if int(fd) in descriptors:
                address = int(mapped[3], 16)
                mappings.append({"path":descriptors[int(fd)], "start":address, "end":address + int(mapped[1]), "offset":int(offset, 0) * 4096})
    pc = registers[15]
    mapping = next(item for item in mappings if item["start"] <= pc < item["end"])
    relative_pc = pc - mapping["start"] + mapping["offset"]
    original = resolve_guest(mapping["path"])
    assembly = []
    with original.open("rb") as stream:
        elf = ELFFile(stream)
        symbols = list(elf.get_section_by_name(".dynsym").iter_symbols())
        fault = next(sym for sym in symbols if sym["st_value"] <= relative_pc < sym["st_value"] + sym["st_size"])
        for name in dict.fromkeys([fault.name, *args.symbols]):
            symbol = next(sym for sym in symbols if sym.name == name)
            address, size = symbol["st_value"], symbol["st_size"]
            segment = next(seg for seg in elf.iter_segments() if seg["p_type"] == "PT_LOAD" and seg["p_vaddr"] <= address < address + size <= seg["p_vaddr"] + seg["p_filesz"])
            stream.seek(segment["p_offset"] + address - segment["p_vaddr"])
            data = stream.read(size)
            assembly.append(f"\n## {name} @ {address:#x}")
            for instruction in Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(data, address):
                marker = " <FAULT>" if instruction.address == relative_pc else ""
                assembly.append(f"{instruction.address:08x} {instruction.mnemonic:8s} {instruction.op_str}{marker}")
    report = {"core":str(core), "pc":hex(pc), "sp":hex(registers[13]), "lr":hex(registers[14]), "library":mapping["path"], "library_relative_pc":hex(relative_pc), "function":fault.name,
              "registers":{("r" + str(i) if i < 13 else ["sp","lr","pc","cpsr","orig_r0"][i-13]):hex(value) for i, value in enumerate(registers)},
              "scope":"The fault site is resolved from the real core and mapped stock library. This is not a reconstructed full call stack."}
    report["run"] = args.run
    (OUTPUT / f"{args.output_prefix}-crash.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUTPUT / f"{args.output_prefix}-fault.asm").write_text("\n".join(assembly) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    framebuffer = OUTPUT / "framebuffer/Xvfb_screen0"
    if framebuffer.exists():
        data = framebuffer.read_bytes()
        header = struct.unpack_from(">25I", data)
        header_size, _, _, _, width, height, _, byte_order, _, _, _, bits_per_pixel, stride, _, red, green, blue, _, _, colors, *_ = header
        if bits_per_pixel == 32 and byte_order == 0 and (red, green, blue) == (0xff0000, 0xff00, 0xff):
            image = Image.frombytes("RGB", (width, height), data[header_size + colors * 12:], "raw", "BGRX", stride)
            image.save(OUTPUT / "camera-xvfb.png")
            print("Xvfb framebuffer:", image.size, "extrema:", image.getextrema())


if __name__ == "__main__": main()
