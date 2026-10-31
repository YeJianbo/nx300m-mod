"""Find stock ELF absolute ARM string references without running camera code."""

import argparse
from bisect import bisect_right
import io
import json
import os
from pathlib import Path
import struct
import sys
import tarfile

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("member")
    parser.add_argument("strings", nargs="*")
    parser.add_argument("--address", nargs="*", type=lambda x: int(x, 0))
    parser.add_argument("--value", nargs="*", type=lambda x: int(x, 0), help="find absolute ARM references to numeric addresses")
    args = parser.parse_args()
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as tar:
        data = tar.extractfile(args.member).read()
    elf = ELFFile(io.BytesIO(data))
    def offset_to_address(offset):
        s = next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_offset'] <= offset < s['p_offset']+s['p_filesz'])
        return s['p_vaddr'] + offset-s['p_offset']
    def read_address(address):
        s = next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr'] <= address < s['p_vaddr']+s['p_filesz'])
        return data[s['p_offset']+address-s['p_vaddr']:]
    if args.address:
        print(json.dumps({hex(a):read_address(a).split(b'\0',1)[0].decode('utf-8','replace') for a in args.address},ensure_ascii=False,indent=2))
        return
    targets = {}
    for address in args.value or []:
        targets[address] = hex(address)
    for name in args.strings:
        cursor = 0
        while (offset := data.find(name.encode()+b'\0', cursor)) >= 0:
            targets[offset_to_address(offset)] = name
            cursor = offset + 1
    functions = {}
    for section in (elf.get_section_by_name('.dynsym'), elf.get_section_by_name('.symtab')):
        if section:
            for symbol in section.iter_symbols():
                if symbol['st_info']['type']=='STT_FUNC' and symbol['st_size'] and not symbol['st_value']&1:
                    functions[symbol['st_value']] = (symbol['st_size'],symbol.name)
    starts = sorted(functions)
    def function(address):
        index = bisect_right(starts,address)-1
        if index < 0: return None
        base=starts[index]; size,name=functions[base]
        return {'name':name,'base':hex(base),'offset':hex(address-base)} if address < base+size else None
    decoder=Cs(CS_ARCH_ARM,CS_MODE_ARM); decoder.detail=True; decoder.skipdata=True
    section=elf.get_section_by_name('.text')
    low={}; hits=[]
    for insn in decoder.disasm(section.data(),section['sh_addr']):
        if insn.id == 0: continue
        operands=insn.operands
        if insn.mnemonic=='movw' and len(operands)==2 and operands[0].type==ARM_OP_REG and operands[1].type==ARM_OP_IMM:
            low[operands[0].reg]=(insn.address,operands[1].imm)
        elif insn.mnemonic=='movt' and len(operands)==2 and operands[0].reg in low:
            previous,value=low[operands[0].reg]
            value |= operands[1].imm << 16
            if insn.address-previous<=64 and value in targets:
                hits.append({'address':hex(insn.address),'type':'movw/movt','target':hex(value),'text':targets[value],'function':function(insn.address)})
            del low[operands[0].reg]
        elif insn.mnemonic.startswith('ldr') and len(operands)>1 and operands[1].type==ARM_OP_MEM and operands[1].mem.base==ARM_REG_PC:
            address=insn.address+8+operands[1].mem.disp
            try: value=struct.unpack_from('<I',read_address(address))[0]
            except (StopIteration,struct.error): continue
            if value in targets:
                hits.append({'address':hex(insn.address),'type':'PC literal','target':hex(value),'text':targets[value],'function':function(insn.address)})
    runtime=Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')/args.member
    data_hits = []
    for data_section in elf.iter_sections():
        if not data_section['sh_flags'] & 2 or data_section['sh_flags'] & 4 or data_section['sh_type'] == 'SHT_NOBITS':
            continue
        section_data = data_section.data()
        for offset in range(0, len(section_data)-3, 4):
            value = struct.unpack_from('<I', section_data, offset)[0]
            if value in targets:
                begin, end = max(0, offset-16), min(len(section_data)//4*4, offset+20)
                data_hits.append({'section':data_section.name,'address':hex(data_section['sh_addr']+offset),
                                  'target':hex(value),'context_start':hex(data_section['sh_addr']+begin),
                                  'context_words':[hex(v) for v in struct.unpack('<'+'I'*((end-begin)//4),section_data[begin:end])]})
    report={'member':args.member,'runtime_file_equals_stock_tar':runtime.read_bytes()==data,'targets':{hex(k):v for k,v in targets.items()},'references':hits,
            'data_references': data_hits,
            'scope':'absolute ARM movw/movt and PC literal references; not complete dataflow, PIC/GOT, indirect or Thumb analysis'}
    (OUT/(Path(args.member).name+'-xrefs.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
