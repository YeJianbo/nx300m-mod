"""Find direct ARM B/BL references, including callers absent from stripped symbols."""
import argparse
from bisect import bisect_right
import io
import json
from pathlib import Path
import struct
from elftools.elf.elffile import ELFFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("names", nargs="*")
    parser.add_argument("--address", nargs="+", type=lambda value: int(value, 0),
                        help="Include numeric ARM branch targets in stripped ELF files")
    args = parser.parse_args()
    if not args.names and not args.address:
        parser.error("Supply symbol names or --address targets")
    elf = ELFFile(io.BytesIO(args.elf.read_bytes()))
    dynamic = list(elf.get_section_by_name(".dynsym").iter_symbols())
    symbols = dynamic[:]
    static = elf.get_section_by_name(".symtab")
    if static:
        symbols.extend(static.iter_symbols())
    targets = {s["st_value"] & ~1: s.name for s in symbols if s["st_value"] and any(n in s.name for n in args.names)}
    targets.update({address: hex(address) for address in args.address or []})
    plt, rel = elf.get_section_by_name(".plt"), elf.get_section_by_name(".rel.plt")
    if plt and rel:
        for index, relocation in enumerate(rel.iter_relocations()):
            name = dynamic[relocation["r_info_sym"]].name
            if any(n in name for n in args.names):
                targets[plt["sh_addr"] + 20 + index * 12] = name + "@plt"
    functions = sorted((s["st_value"] & ~1, s["st_size"], s.name) for s in symbols if s["st_info"]["type"] == "STT_FUNC" and s["st_size"])
    starts = [s[0] for s in functions]
    section = elf.get_section_by_name(".text")
    data = section.data()
    references = []
    for offset in range(0, len(data) - 3, 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if word & 0x0e000000 != 0x0a000000 or word >> 28 == 15:
            continue
        displacement = word & 0xffffff
        if displacement & 0x800000:
            displacement -= 0x1000000
        address = section["sh_addr"] + offset
        target = address + 8 + displacement * 4
        if target not in targets:
            continue
        index = bisect_right(starts, address) - 1
        owner = None
        if index >= 0 and address < functions[index][0] + functions[index][1]:
            owner = functions[index][2]
        references.append({"address": hex(address), "kind": "BL" if word & 0x1000000 else "B", "target": hex(target), "name": targets[target], "caller": owner})
    print(json.dumps({"source": str(args.elf), "references": references,
                      "scope": "Direct ARM branches only; no indirect calls, Thumb, or full reachability analysis."}, indent=2))


if __name__ == "__main__":
    main()
