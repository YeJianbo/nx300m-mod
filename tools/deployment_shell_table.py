"""Read stock release-shell command registrations; never execute the camera ELF."""
import io
import json
import os
from pathlib import Path
import re
import struct
import sys

os.environ.setdefault("CONDA_PREFIX", sys.prefix)
from elftools.elf.elffile import ELFFile

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "outputs/deployment/stock/usr/bin/di-camera-app-nx300"
OUT = BASE / "outputs/deployment"


def main():
    raw = SOURCE.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    segments = list(elf.iter_segments())

    def cstring(address):
        for segment in segments:
            if segment["p_type"] == "PT_LOAD" and segment["p_vaddr"] <= address < segment["p_vaddr"] + segment["p_filesz"]:
                offset = segment["p_offset"] + address - segment["p_vaddr"]
                return raw[offset:raw.index(b"\0", offset)].decode("utf-8", "replace")
        return None

    lines = (OUT / "app-shell-init.asm").read_text(encoding="utf-8-sig").splitlines()
    registrations = []
    pending = None
    registers = {}
    for line in lines:
        match = re.match(r"([0-9a-f]+) (movw|movt)\s+(r[0-9]+), #(?:0x([0-9a-f]+)|(\d+))", line)
        if match:
            pc, op, reg, hexdigits, digits = match.groups()
            value = int(hexdigits, 16) if hexdigits else int(digits)
            registers[reg] = value if op == "movw" else (registers.get(reg, 0) & 0xffff) | (value << 16)
            if op == "movt" and reg == "r3" and pending:
                handler = registers[reg]
                if 0x1ff000 <= handler < 0x210000:
                    registrations.append({"name": pending, "handler": hex(handler), "handler_load": "0x" + pc})
                    pending = None
        if "_ZNSsC1EPKcRKSaIcE@plt" in line and "r1" in registers:
            pending = cstring(registers["r1"])
    strings = {hex(address): cstring(address) for address in (
        0x270898, 0x27089c, 0x2708ac, 0x2708bc, 0x2708cc,
        0x2708e8, 0x270908, 0x27b468, 0x27b480, 0x25b1b4, 0x25cf50,
    )}
    relocation_names = []
    for section in elf.iter_sections():
        if section["sh_type"] not in ("SHT_REL", "SHT_RELA"):
            continue
        symbols = elf.get_section(section["sh_link"])
        for relocation in section.iter_relocations():
            if relocation["r_offset"] == 0x31cb80:
                relocation_names.append(symbols.get_symbol(relocation["r_info_sym"]).name)
    start_script = next(s["st_value"] for s in elf.get_section_by_name(".dynsym").iter_symbols() if s.name == "start_script")
    section = elf.get_section_by_name(".text")
    text_data = section.data()
    functions = [s for s in elf.get_section_by_name(".dynsym").iter_symbols()
                 if s["st_info"]["type"] == "STT_FUNC" and s["st_size"]]
    callers = []
    for offset in range(0, len(text_data) - 3, 4):
        word = struct.unpack_from("<I", text_data, offset)[0]
        if word & 0x0e000000 != 0x0a000000 or word >> 28 == 15:
            continue
        displacement = word & 0xffffff
        if displacement & 0x800000:
            displacement -= 0x1000000
        address = section["sh_addr"] + offset
        if address + 8 + 4 * displacement == start_script:
            owners = [s.name for s in functions if s["st_value"] <= address < s["st_value"] + s["st_size"]]
            callers.append({"address": hex(address), "kind": "BL" if word & 0x01000000 else "B", "functions": owners})
    report = {"source": str(SOURCE.relative_to(BASE)), "registrations": registrations,
              "dev_mode_strings": strings, "input_FILE_relocations": relocation_names,
              "start_script_plt": hex(start_script), "start_script_direct_ARM_callers": callers,
              "limits": "Pattern extraction from the known ARM initialization function; review disassembly before claiming command semantics."}
    (OUT / "stock-shell-table.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
