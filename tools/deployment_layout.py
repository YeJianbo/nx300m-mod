"""Review original SLP checksums and libmisc mapping without changing either input."""
import io
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import zlib
from elftools.elf.elffile import ELFFile
os.environ.setdefault("CONDA_PREFIX", sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment"


def main():
    with (ROOT / "work/stock-1.15/nx300m.bin").open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        magic, version, model, date, pcache, pcache_offset, reserved = struct.unpack_from("<4s8s16s16sbI15s", data)
        assert magic == b"SLP\0" and model.rstrip(b"\0") == b"NX300M"
        parts = []
        names = ("vImage", "D4_IPL.bin", "D4_PNLBL.bin", "uImage", "platform.img")
        for index, name in enumerate(names):
            length, expected_crc, offset, part_magic = struct.unpack_from("<4I", data, 64 + index * 16)
            parts.append({"name": name, "header_offset": 64 + index * 16, "offset": offset, "length": length, "crc32": expected_crc, "magic": hex(part_magic)})
        if pcache == 1:
            length, expected_crc, offset, part_magic = struct.unpack_from("<4I", data, pcache_offset)
            parts.append({"name": "pcache.list", "header_offset": pcache_offset, "offset": offset, "length": length, "crc32": expected_crc, "magic": hex(part_magic)})
        else:
            raise RuntimeError("This review handles only the verified NX300M 1.15 v1 layout")
        for part in parts:
            assert 0 <= part["offset"] <= part["offset"] + part["length"] <= len(data)
            crc = 0
            for cursor in range(part["offset"], part["offset"] + part["length"], 1024 * 1024):
                crc = zlib.crc32(data[cursor:min(cursor + 1024 * 1024, part["offset"] + part["length"])], crc)
            part["standard_zlib_crc32"] = crc
            part["standard_zlib_matches"] = crc == part["crc32"]
            part["complemented_zlib_crc32"] = crc ^ 0xffffffff
            part["complemented_zlib_matches"] = (crc ^ 0xffffffff) == part["crc32"]
        firmware = {"size": len(data), "version": version.rstrip(b"\0").decode(), "model": model.rstrip(b"\0").decode(),
                    "date": date.rstrip(b"\0").decode(), "header_hex": data[:144].hex(), "partitions": parts,
                    "limits": "Parsed headers and checksum observations only. Bootloader acceptance, signature policy and upgrade behavior require separate evidence."}
    raw = (OUT / "stock/usr/lib/libmisc.so").read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    exports = []
    for symbol in elf.get_section_by_name(".dynsym").iter_symbols():
        if symbol["st_shndx"] != "SHN_UNDEF" and symbol.name:
            exports.append({"name": symbol.name, "value": symbol["st_value"], "size": symbol["st_size"], "type": symbol["st_info"]["type"]})
    mappings = [{"type": s["p_type"], "offset": s["p_offset"], "vaddr": s["p_vaddr"], "filesz": s["p_filesz"], "memsz": s["p_memsz"], "flags": s["p_flags"], "align": s["p_align"]} for s in elf.iter_segments()]
    sections = [{"name": s.name, "offset": s["sh_offset"], "addr": s["sh_addr"], "size": s["sh_size"], "flags": s["sh_flags"]} for s in elf.iter_sections()]
    dynamic = list(elf.get_section_by_name(".dynsym").iter_symbols())
    relocations = list(elf.get_section_by_name(".rel.plt").iter_relocations())
    system_index = next(i for i, r in enumerate(relocations) if dynamic[r["r_info_sym"]].name == "system")
    system_plt = elf.get_section_by_name(".plt")["sh_addr"] + 20 + 12 * system_index
    append_offset = (len(raw) + 15) & ~15
    command = b"test -f /mnt/mmc/nx300m-mod/start.sh && /bin/sh /mnt/mmc/nx300m-mod/start.sh >/dev/null 2>&1 &\0"
    def branch(source, destination, link=False):
        displacement = destination - source - 8
        assert displacement % 4 == 0 and -(1 << 25) <= displacement < (1 << 25)
        return (0xeb000000 if link else 0xea000000) | ((displacement // 4) & 0xffffff)
    stub = struct.pack("<5I", 0xe92d4010, 0xe28f0008, branch(append_offset + 8, system_plt, True), 0xe3a00000, 0xe8bd8010)
    payload_size = len(stub) + len(command)
    rx_index, rx = next((i, s) for i, s in enumerate(elf.iter_segments()) if s["p_type"] == "PT_LOAD" and s["p_flags"] == 5)
    assert rx["p_offset"] == rx["p_vaddr"] == 0
    assert append_offset + payload_size < min(s["p_vaddr"] for s in elf.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_vaddr"] > 0)
    phoff = elf["e_phoff"] + rx_index * elf["e_phentsize"]
    proposed = {"status": "review-only; no modified ELF or firmware generated", "entry": "start_script",
                "entry_file_offset": 0x1218, "expected_entry_hex": raw[0x1218:0x1220].hex(),
                "replace_first_word_hex": struct.pack("<I", branch(0x1218, append_offset)).hex(),
                "remaining_bx_lr_unchanged": True, "append_offset": append_offset, "alignment_padding_bytes": append_offset-len(raw),
                "stub_hex": stub.hex(), "stub_disassembly": [f"{i.address:#x} {i.mnemonic} {i.op_str}" for i in Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(stub, append_offset)],
                "command": command[:-1].decode(), "payload_size": payload_size, "new_ELF_size": append_offset + payload_size,
                "RX_segment_updates": [{"field": "p_filesz", "file_offset": phoff+16, "old": rx["p_filesz"], "proposed": append_offset+payload_size},
                                       {"field": "p_memsz", "file_offset": phoff+20, "old": rx["p_memsz"], "proposed": append_offset+payload_size}],
                "system_PLT": system_plt,
                "ABI": "ARM EABI32; no inputs read; preserve r4/LR and 8-byte stack alignment; return 0 after launching fixed background shell command",
                "limits": ["Not executed or linked by the original loader yet", "SD script must enforce single instance and explicit readiness", "Does not inject LD_PRELOAD into the already running app", "Card removal and poweroff lifecycle not implemented by this stub", "Original stop_script remains inert for this independent background process"]}
    report = {"firmware": firmware, "libmisc": {"size": len(raw), "machine": elf["e_machine"], "flags": elf["e_flags"], "segments": mappings, "sections": sections, "exports": exports,
              "start_script_file_offset": next(s["p_offset"] + 0x1218 - s["p_vaddr"] for s in elf.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_vaddr"] <= 0x1218 < s["p_vaddr"] + s["p_filesz"]),
              "start_script_original_hex": raw[0x1218:0x1220].hex()}}
    (OUT / "stock-layout-review.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (OUT / "start-script-patch-review.json").write_text(json.dumps(proposed, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"firmware": firmware, "start_script_patch_review": proposed}, indent=2))


if __name__ == "__main__":
    main()
