"""Extract NX300M 1.15 native PCSE code and direct-call evidence, without execution."""
import argparse
import io
import json
from pathlib import Path
import struct

from inspect_recipe_tables import all_symbols, disassemble, read_symbol
from inspect_gamma_hook import Stock
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]


def is_pcse(name):
    return '_pcse' in name.lower() or 'PcseD4' in name


def is_related(name):
    return is_pcse(name) or 'CIpcmChromaD4' in name or 'ipcm_chroma_lut' in name


def inspect_library(path, relative):
    elf = ELFFile(io.BytesIO(path.read_bytes()))
    symbols = all_symbols(elf)
    selected = [name for name, symbol in symbols.items()
                if is_related(name) and symbol['st_info']['type'] == 'STT_FUNC']
    # Include the stock node's dispatch and index selection, not just UDD writers.
    if 'libiq-' in relative:
        selected += [name for name, symbol in symbols.items()
                     if any(cls in name for cls in ('CIpcParameterNode', 'CIpcLiveviewParamD4', 'CIpcParameterList'))
                     and any(part in name for part in ('Update', 'Reset', 'C1Ev', 'AddIpcParameterNode'))
                     and symbol['st_info']['type'] == 'STT_FUNC']
    if 'libcapture-' in relative:
        selected += [name for name, symbol in symbols.items()
                     if ('CapIfDspSet' in name or 'CapIfDspParamSetIPCm' in name)
                     and symbol['st_info']['type'] == 'STT_FUNC']
    listing, metadata = disassemble(elf, symbols, selected)
    targets = {symbol['st_value'] & ~1: name for name, symbol in symbols.items()
               if is_related(name) and symbol['st_info']['type'] == 'STT_FUNC'}
    dynamic, plt, relplt = (elf.get_section_by_name(n) for n in ('.dynsym', '.plt', '.rel.plt'))
    if plt is not None and relplt is not None:
        for index, relocation in enumerate(relplt.iter_relocations()):
            name = dynamic.get_symbol(relocation['r_info_sym']).name
            if is_related(name):
                targets[plt['sh_addr'] + 20 + index * 12] = name + '@plt'
    # Scan bounded function bodies; this is not a proof of runtime reachability.
    callers, visited = [], set()
    for name, symbol in symbols.items():
        if symbol['st_info']['type'] != 'STT_FUNC' or not symbol['st_size']:
            continue
        key = (symbol['st_value'], symbol['st_size'])
        if key in visited:
            continue
        visited.add(key)
        data, _, _ = read_symbol(elf, symbol)
        decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB if symbol['st_value'] & 1 else CS_MODE_ARM)
        decoder.skipdata = True
        for instruction in decoder.disasm(data, symbol['st_value'] & ~1):
            if instruction.mnemonic not in ('bl', 'blx', 'b') or not instruction.op_str.startswith('#0x'):
                continue
            target = targets.get(int(instruction.op_str[1:], 16))
            if target:
                callers.append({'caller': name, 'caller_address': hex(symbol['st_value'] & ~1),
                                'instruction': hex(instruction.address), 'target': target})
    objects = []
    for name, symbol in symbols.items():
        if not is_related(name) or symbol['st_info']['type'] != 'STT_OBJECT':
            continue
        raw, section, offset = read_symbol(elf, symbol)
        objects.append({'symbol': name, 'address': hex(symbol['st_value']), 'size_bytes': len(raw),
                        'section': section, 'file_offset': hex(offset), 'hex': raw.hex(),
                        'u32': list(struct.unpack('<' + 'I' * (len(raw) // 4), raw)) if len(raw) % 4 == 0 else None})
    imports = [s.name for s in dynamic.iter_symbols()
               if s['st_shndx'] == 'SHN_UNDEF' and is_related(s.name)]
    return {'library': relative, 'functions': metadata, 'imports': imports,
            'direct_call_sites': callers, 'objects': objects}, listing


def reviewed_findings(root):
    iq = Stock(root / 'usr/lib/libiq-fw-slpcam.so')
    got = (0x3411d8 + 8 + iq.literal(0x3411c8)[1]) & 0xffffffff
    slot = (got + iq.literal(0x3412bc)[1]) & 0xffffffff
    expected = '_ZN11CIpcmPcseD415m_uiPrimaryGainE'
    binding = iq.relocs[slot]
    if binding['symbol'] != expected or binding['type'] != 21:
        raise ValueError('Stock PCSE primary table GOT identity changed')
    defaults_address = (got + iq.literal(0x3411d0)[1]) & 0xffffffff
    defaults = list(struct.unpack('<13I', iq.read(defaults_address, 52)))
    table = iq.table(expected, 12)
    return {
        'primary_abi': {
            'size_bytes': 52, 'selector_offset': 0, 'gain_offset': 4,
            'count': 12, 'element_width': 4,
            'sdk_field_order': ['r','g','b','c','m','y','o','cg','v','yg','cb','p'],
            'selector': {'0': 'IPCM_PHC_PGAIN (hue)', '1': 'IPCM_PSC_PGAIN (saturation)'},
            'accepted_signed_words': [-4095, 4095],
            'range_evidence': 'UDD 0x4a200-0x4a3b4: each word <=0xfff unsigned, or CMN(word,0x1000) is HI; -4096 is rejected.',
            'packing': 'Two low-13-bit fields per word at bits 0 and 16; six words per selector.',
            'selector0_offsets': [hex(0x108+4*i) for i in range(6)],
            'selector1_offsets': [hex(0x120+4*i) for i in range(6)],
            'physical_gain_scale': None, 'hue_units': None,
            'note': '13-bit packing is not evidence for Q12 pixel gains or evenly spaced HSV sectors.',
        },
        'primary_table_binding': {'got_base':hex(got), 'slot':hex(slot), 'relocation':binding,
                                  'instructions':['0x3411c8','0x3411d8','0x3412bc','0x3412cc']},
        'stock_primary_table': table,
        'stock_setparam': {
            'address':'0x3411c0',
            'table_index': 'GetIndex input+0x58 ==13 selects row1, ==14 selects row2, otherwise row0. Input+0x5c supplies the second subindex for 13/14.',
            'table_stride_bytes':48,
            'selector0_hue':'Twelve zero words, forwarded at 0x341448.',
            'selector1_saturation':'Each table word is multiplied by second_subindex+1, then unsigned-divided by 5; forwarded at 0x341454.',
            'software_division_evidence':'UMULL with 0xcccccccd and high-word shift by 2 at 0x34131c-0x34140c.',
            'defaults_address':hex(defaults_address),
            'hue_protection_range':defaults[:5], 'saturation_protection_range':defaults[5:10],
            'gray_threshold_initial':defaults[10:13],
            'setter_errors_checked':False,
        },
        'native_update_path': {
            'liveview_constructor':'0x3407a0 constructs CIpcmPcseD4; 0x3407ac adds it to the native liveview parameter list.',
            'base_update':'0x350dc0 calls vptr+0x14 GetIndex, compares self+4, and only on change calls vptr+0x10 SetParam.',
            'base_reset':'0x350e0c sets self+4 to 0xffffffff.',
            'pcse_reset':'0x341470 additionally sends CapIfDspSet IDs 0x30031..0x30038.',
            'pcse_reset_values':[1,0,0,0,1,0,0,0],
            'capture_bridge':'CapIfDspSet 0x1beb20 -> CapIfDspParamSetIPCm 0x166cf4 -> ipcm_set_pcse_sw 0x185a58 -> ipcm_liveview_pcse_on/off.',
            'capture_mapping_note':'0x30031..0x30037 map to selector0..6; 0x30038 maps to selector3, not SDK selector7 (instructions 0x185b30-0x185b34). Preserve this observed discrepancy in any integration design.',
            'runtime_observed':False,
        },
        'chroma_lut': {
            'setter':'ipcm_chroma_lut 0x4b4f4', 'selectors':[0,1],
            'sdk_names':['IPCM_ORANGE_TBL','IPCM_CYAN_TBL'],
            'copy_bytes_per_selector':64,
            'copy_instructions':['0x4b570-0x4b574','0x4b594-0x4b598'],
            'unknowns':['Addressing coordinate','Per-entry response','Interaction with PCSE'],
            'not_established':'These two 64-byte chroma gain tables are not established as a general 3D LUT.',
        },
        'remaining_model_questions': ['Native color coordinates and sector interpolation',
                                      'Primary hue/saturation units and neutral response',
                                      'Gray/skin protection transfer functions',
                                      'Pixel stage order, clipping and fixed-point rounding',
                                      'Mode-dependent enable state and hardware scheduling'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT / 'outputs/metol/ipcm-pcse.json')
    args = parser.parse_args()
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    root = Path(config['rootfs'])
    libraries, listings = [], []
    for relative in ('usr/lib/libudd.so.3.1', 'usr/lib/libiq-fw-slpcam.so',
                     'usr/lib/libcapture-fw-slpcam-nx300.so'):
        info, listing = inspect_library(root / relative, relative)
        libraries.append(info)
        listings.append('# Library: ' + relative + '\n' + listing)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix('.asm').write_text('\n'.join(listings), encoding='utf-8')
    args.output.write_text(json.dumps({'scope': 'Static declarations, symbol-bounded disassembly and direct calls; no camera or ISP execution.',
                                     'reviewed_findings':reviewed_findings(root),
                                     'libraries': libraries}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output),
                      'libraries': [{'name': i['library'], 'functions': len(i['functions']),
                                     'call_sites': len(i['direct_call_sites'])} for i in libraries]}))


if __name__ == '__main__':
    main()
