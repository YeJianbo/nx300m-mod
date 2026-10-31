"""Disassemble the stock DRIMeIV LUT path and extract its named table objects."""

import json
import struct
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

PROJECT = Path(__file__).resolve().parents[1]
TARGETS = {
    "libudd.so.3.1": ["ipcs_3d_lut", "ipcs_hl_3d_lut_onoff"],
    "libcapture-fw-slpcam-nx300.so": [
        "_ZN18CDDWrapperIPCS_B2Y17SetIQParam_3D_LUTEP18stIPCS_B2Y_IQParam",
    ],
    "libiq-fw-slpcam.so": [
        "_ZN12CIpcs3DLutD48SetParamEj",
        "_ZN12CIpcs3DLutD48GetIndexEPv",
        "_ZN12CIpcs3DLutD4C1Ev",
    ],
}
TABLES = {f"_ZN12CIpcs3DLutD47m_s{channel}LutE": channel for channel in "RGB"}


def contents(elf, address, size):
    for section in elf.iter_sections():
        start = section["sh_addr"]
        if section["sh_type"] != "SHT_NOBITS" and start <= address and address + size <= start + section["sh_size"]:
            return section.data()[address - start:address - start + size]
    raise ValueError(f"No file-backed section at {address:#x} for {size} bytes")


def main():
    config = json.loads((PROJECT / "project.local.json").read_text(encoding="utf-8"))
    rootfs = PROJECT / config["rootfs"]
    output = PROJECT / "outputs"
    output.mkdir(exist_ok=True)
    lines = ["Stock NX300M 1.15 LUT code (ELF virtual addresses, not live process addresses)."]
    tables = {}
    for filename, wanted in TARGETS.items():
        with (rootfs / "usr/lib" / filename).open("rb") as stream:
            elf = ELFFile(stream)
            symbols = {}
            for name in (".dynsym", ".symtab"):
                section = elf.get_section_by_name(name)
                if isinstance(section, SymbolTableSection):
                    symbols.update({sym.name: sym for sym in section.iter_symbols() if sym["st_shndx"] != "SHN_UNDEF"})
            for name in wanted:
                sym = symbols[name]
                raw_address = sym["st_value"]
                address = raw_address & ~1
                decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB if raw_address & 1 else CS_MODE_ARM)
                data = contents(elf, address, sym["st_size"])
                lines.append(f"\n## {filename}: {name} @ {address:#x}, {len(data)} bytes")
                for instruction in decoder.disasm(data, address):
                    lines.append(f"{instruction.address:08x}  {instruction.bytes.hex():10s} {instruction.mnemonic:8s} {instruction.op_str}")
            if filename == "libiq-fw-slpcam.so":
                for name, channel in TABLES.items():
                    sym = symbols[name]
                    data = contents(elf, sym["st_value"], sym["st_size"])
                    tables[channel] = {
                        "symbol": name,
                        "address": hex(sym["st_value"]),
                        "size_bytes": len(data),
                        "uint16_le": list(struct.unpack("<" + "H" * (len(data) // 2), data)),
                    }
    (output / "lut-disassembly.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "lut-tables.json").write_text(json.dumps(tables, indent=2) + "\n", encoding="utf-8")
    print("Disassembly: outputs/lut-disassembly.txt")
    for channel, table in tables.items():
        print(f"{channel}: {table['size_bytes']} bytes; uint16 count={len(table['uint16_le'])}; first 12={table['uint16_le'][:12]}")


if __name__ == "__main__":
    main()
