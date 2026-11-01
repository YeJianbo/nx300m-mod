"""Trace stock SmartFilter direct calls and LUT-capable IPCS operations."""
import collections
import hashlib
import json
from pathlib import Path

from inspect_gamma_hook import Stock
from inspect_recipe_tables import disassemble

ROOT=Path(__file__).resolve().parents[1]
LIBRARIES=('libcapture-fw-slpcam-nx300.so','libdi-alg-smartfilter.so',
           'libdi-alg-ipplatform.so','libdi-alg-selectivecolor.so',
           'libdi-alg-smartart.so','libudd.so.3.1')


def inspect():
    rootfs=Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    stocks={name:Stock(rootfs/'usr/lib'/name) for name in LIBRARIES}
    functions={};exports={};plts={};edges={};indirect={}
    for library,stock in stocks.items():
        for name,symbol in stock.symbols.items():
            if symbol['st_info']['type']!='STT_FUNC' or not symbol['st_size']:continue
            key=(library,int(symbol['st_value'])&~1)
            functions.setdefault(key,{'library':library,'address':hex(key[1]),
                'size':int(symbol['st_size']),'symbol':name,'aliases':[]})['aliases'].append(name)
            exports.setdefault(name,[]).append(key)
        plt=stock.elf.get_section_by_name('.plt');rel=stock.elf.get_section_by_name('.rel.plt')
        if plt is not None and rel is not None:
            dyn=stock.elf.get_section(rel['sh_link'])
            plts[library]={int(plt['sh_addr'])+20+i*12:dyn.get_symbol(r['r_info_sym']).name
                           for i,r in enumerate(rel.iter_relocations())}
        else:plts[library]={}

    def scan(key):
        if key in edges:return edges[key]
        library,address=key;stock=stocks[library];fn=functions[key]
        found=[];unknown=[]
        for ins in stock.decoder.disasm(stock.read(address,fn['size']),address):
            if ins.mnemonic not in ('bl','blx','b'):continue
            if not ins.op_str.startswith('#'):
                if ins.mnemonic!='b':unknown.append(hex(ins.address))
                continue
            target=int(ins.op_str[1:],0)
            if ins.mnemonic=='b' and address<=target<address+fn['size']:continue
            name=plts[library].get(target)
            destination=None
            if name:
                owners=exports.get(name,[])
                destination=next((v for v in owners if v[0]==library),owners[0] if owners else None)
            elif (library,target) in functions:
                destination=(library,target);name=functions[destination]['symbol']
            else:continue
            found.append({'call':hex(ins.address),'instruction':ins.mnemonic,'target_symbol':name,
                          'target_library':destination[0] if destination else None,
                          'target_address':hex(destination[1]) if destination else None})
        edges[key]=found;indirect[key]=unknown
        return found

    roots=[key for key,fn in functions.items() if any('ADE_SmartFilter_YccProcess' in n for n in fn['aliases'])]
    queue=collections.deque(roots);visited=set()
    while queue:
        key=queue.popleft()
        if key in visited:continue
        visited.add(key)
        for edge in scan(key):
            owner=edge['target_library']
            if owner and owner!='libudd.so.3.1':
                queue.append((owner,int(edge['target_address'],16)))

    def path_to(substring,start=None,allow_udd=False):
        queue=collections.deque((key,[]) for key in (roots if start is None else start));seen=set()
        while queue:
            key,path=queue.popleft()
            if key in seen:continue
            seen.add(key)
            if len(path)>=8:continue
            for edge in scan(key):
                item={'from':functions[key]['symbol'],'library':key[0],**edge}
                if substring in edge['target_symbol']:return path+[item]
                if edge['target_library'] and (allow_udd or edge['target_library']!='libudd.so.3.1'):
                    queue.append(((edge['target_library'],int(edge['target_address'],16)),path+[item]))
        return None

    platform=stocks['libdi-alg-ipplatform.so'];udd=stocks['libudd.so.3.1']
    _,delta=platform.literal(0x4bdc);pic_base=(0x4be0+8+delta)&0xffffffff
    _,offset=platform.literal(0x4eac);flag_slot=(pic_base+offset)&0xffffffff
    flag=platform.relocs[flag_slot]
    if flag['symbol']!='ADE_IP_switch_RGB3D_LUT' or flag['type']!=21:
        raise ValueError('Unexpected ADE 3D LUT flag binding')
    instruction=udd.instruction(0x50810)
    if (instruction.mnemonic,instruction.op_str)!=('ldr','r3, [r3, #0x1a0]'):
        raise ValueError('Unreviewed RGB3D_LUT_SW operation offset')
    switch_resets=[]
    _,delta=platform.literal(0x39b4);base=(0x39b8+8+delta)&0xffffffff
    for address in (0x39bc,0x39cc,0x39dc,0x39ec,0x39fc,0x3a0c):
        _,offset=platform.literal(address);slot=(base+offset)&0xffffffff
        switch_resets.append({'literal_load':hex(address),'slot':hex(slot),
                              'relocation':platform.relocs[slot]})
    udd_root=[exports['ipcs_ycc_correction_to_ycc_operation'][0]]
    native_path=path_to('ipcs_hl_3d_lut_onoff',udd_root,True)
    if not native_path:raise ValueError('Missing UDD 3D LUT switch path')
    capture_nodes=[{**fn,'direct_calls':scan(key),'indirect_call_sites':indirect[key]}
                   for key,fn in functions.items() if key[0].startswith('libcapture') and
                   'CLiveviewNodeSmartFilter' in fn['symbol']]
    listings=[];records=[]
    for library,stock in stocks.items():
        if library.startswith('libcapture'):
            names=[name for name in stock.symbols if any(token in name for token in (
                'CLiveviewNodeSmartFilter','setLiveviewPathSmartFilter','setLiveviewNormalPathSmartFilter'))
                and stock.symbols[name]['st_info']['type']=='STT_FUNC']
        else:
            names=[name for name in stock.symbols if any(token in name for token in (
                'ADE_SmartFilter','ADE_Ipcs','ADE_RGBTONE','ADE_Suchae','ADE_ProcessLut',
                'ycc_correction_to_ycc','ipcs_3d_lut','ipcs_hl_3d_lut_onoff','ipcs_wait_1tile_done'))
                and stock.symbols[name]['st_info']['type']=='STT_FUNC']
        listing,selected=disassemble(stock.elf,stock.symbols,names)
        listing=listing.replace('disassembly of stock libiq-fw-slpcam.so.','disassembly of stock '+library+'.')
        listings.append('# '+library+'\n'+listing)
        records.append({'library':library,'sha256':hashlib.sha256(stock.path.read_bytes()).hexdigest(),
                        'selected_functions':selected})
    nodes=[{**functions[key],'direct_calls':edges.get(key,[]),'indirect_call_sites':indirect.get(key,[])}
           for key in sorted(visited)]
    return {'scope':'Static direct-call graph from original NX300M 1.15; no camera, pixel processing or firmware changes',
        'libraries':records,'roots':[functions[k] for k in roots],'reachable_functions':nodes,
        'capture_nodes':capture_nodes,
        'ade_3d_switch':{'object':'ADE_IP_switch_RGB3D_LUT','object_address':hex(platform.symbols['ADE_IP_switch_RGB3D_LUT']['st_value']),
            'got_slot':hex(flag_slot),'read_literal':'0x4eac','read_flag':'0x4eb4',
            'store_boolean':'0x4ecc','stack_struct_base':'fp-0x234','stack_field':'fp-0x94',
            'operation_field_offset':'0x1a0','switch_initialization':switch_resets,
            'native_enable_path':native_path},
        'paths':{name:path_to(name) for name in ('ADE_Ipcs_Ycc_correct_Param_SET',
            'ipcs_ycc_correction_to_ycc_operation','ipcs_3d_lut','ADE_Ipcs_Open')},
        'limitations':['Static edges show possible calls, not mode-specific execution.',
            'Indirect calls, unresolved external libraries and DMA completion semantics require separate analysis.',
            'Calling a YCC operation does not establish that its RGB3D_LUT_SW is enabled.']},'\n'.join(listings)


def main():
    result,listing=inspect();output=ROOT/'outputs/metol/smartfilter-lut'
    output.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    output.with_suffix('.asm').write_text(listing,encoding='utf-8')
    print(json.dumps({'output':str(output.with_suffix('.json')),'reachable_functions':len(result['reachable_functions']),
                      'roots':result['roots'],'paths':result['paths']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
