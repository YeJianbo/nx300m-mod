"""Reconstruct candidate ARM literal/PIC strings for static review; no ELF execution."""
import argparse
import io
import json
import os
from pathlib import Path
import struct
import sys

os.environ.setdefault("CONDA_PREFIX", sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
from elftools.elf.elffile import ELFFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("functions", nargs="*")
    parser.add_argument("--range", nargs=2, type=lambda value: int(value, 0))
    args = parser.parse_args()
    if not args.functions and not args.range:
        parser.error("supply function names or --range START SIZE")
    raw = args.elf.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]

    def read(address, size=256):
        for s in segments:
            if s["p_vaddr"] <= address < s["p_vaddr"] + s["p_filesz"]:
                offset = s["p_offset"] + address - s["p_vaddr"]
                return raw[offset:offset + min(size, s["p_filesz"] - address + s["p_vaddr"])]
        return b""

    def word(address):
        data = read(address, 4)
        return struct.unpack("<I", data)[0] if len(data) == 4 else None

    def string(address):
        data = read(address)
        if b"\0" not in data:
            return None
        value = data.split(b"\0", 1)[0]
        return value.decode("ascii") if len(value) >= 3 and all(32 <= b < 127 or b in (9, 10, 13) for b in value) else None

    symbols = {}
    for name in (".dynsym", ".symtab"):
        section = elf.get_section_by_name(name)
        if section:
            for s in section.iter_symbols():
                if s["st_info"]["type"] == "STT_FUNC" and s["st_size"] and not s["st_value"] & 1:
                    symbols[s["st_value"]] = s
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.detail = True
    decoder.skipdata = True
    selected = [(address, s["st_size"], s.name) for address, s in symbols.items() if any(name in s.name for name in args.functions)]
    if args.range:
        selected.append((*args.range, "address-range"))
    for address, size, name in selected:
        print(f"## {name} {address:#x}")
        registers = {}
        for insn in decoder.disasm(read(address, size), address):
            if not insn.id:
                continue
            ops = insn.operands
            get = lambda o: o.imm if o.type == ARM_OP_IMM else insn.address + 8 if o.type == ARM_OP_REG and o.reg == ARM_REG_PC else registers.get(o.reg) if o.type == ARM_OP_REG else None
            value = None
            if insn.mnemonic in ("mov", "movw") and len(ops) == 2:
                value = get(ops[1])
            elif insn.mnemonic == "movt" and len(ops) == 2:
                value = (registers.get(ops[0].reg, 0) & 65535) | (ops[1].imm << 16)
            elif insn.mnemonic == "add" and len(ops) == 3:
                a, b = get(ops[1]), get(ops[2])
                if a is not None and b is not None:
                    value = (a + b) & 0xffffffff
            elif insn.mnemonic == "ldr" and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
                mem = ops[1].mem
                base = insn.address + 8 if mem.base == ARM_REG_PC else registers.get(mem.base)
                index = registers.get(mem.index) if mem.index else 0
                if base is not None and index is not None:
                    value = word((base + index + mem.disp) & 0xffffffff)
            if ops and ops[0].type == ARM_OP_REG and insn.mnemonic in ("ldr", "mov", "movw", "movt", "add"):
                if value is None:
                    registers.pop(ops[0].reg, None)
                else:
                    registers[ops[0].reg] = value
                    message = string(value)
                    if message:
                        print(f"{insn.address:#010x} {insn.mnemonic} {insn.op_str} => {value:#x} {json.dumps(message, ensure_ascii=False)}")
            if insn.mnemonic in ("bl", "blx"):
                for reg in list(registers):
                    if decoder.reg_name(reg) in ("r0", "r1", "r2", "r3", "ip", "lr"):
                        registers.pop(reg, None)
        print()
    print("Scope: linear constant reconstruction only; branches and indirect dataflow require disassembly review.")


if __name__ == "__main__":
    main()
