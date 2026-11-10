"""Read stock ARM ELFs and record runtime-control interfaces; never runs target code."""
import argparse
from bisect import bisect_right
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
os.environ.setdefault('CONDA_PREFIX',sys.prefix)
from capstone import Cs,CS_ARCH_ARM,CS_MODE_ARM,CS_MODE_THUMB
from capstone.arm import ARM_OP_IMM,ARM_OP_MEM,ARM_REG_PC
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/controls/runtime-interfaces'
PATTERNS=re.compile(r'CIQDataRepeater.*(ISOMax|ISOSetting|SetParameter|m_pInstance)|'
    r'CAeInfoIF.*(calcTv|AeUpdateProgramLineInfo|AeUpdateSystemInfo|CaptureMode|ISOMax|m_pInstance)|'
    r'CAeProgramLine.*(captureProgramLine_Common|calcProgramLine_Capture|StartReset|AeProgramLineInfo|calcSvshiftForIsoAuto|AeProgramLineForCapture)|'
    r'AeMathCalcProgramLine|'
    r'CDataManagementService.*(GetInstance|GetUserData|SetUserData)|'
    r'CAPPGUIModeView|UI_Manager_ModeDial|UI_Event_ModeDial|UI_(Get|Set)_Value|UI_Timer_(Start|Stop)|'
    r'gUI_Key_.*(Custom|Delete)|[Pp]ower.*([Tt]imer|[Aa]ctiv|[Rr]eset|[Tt]ime)|'
    r'([Rr]eset|[Rr]estart|[Ss]tart|[Ss]top).*([Pp]ower|[Aa]uto[Pp]ower)|'
    r'CPreProcessorD4.*(CsParam|m_pInstance)|CIQLensInfo.*m_pInstance|'
    r'CLensOperation.*(IsModuleReady|m_pInstance)|'
    r'CIQDataRepeater.*(GetShootingMode|GetCaptureMode|GetMovieAE|Get3DMode)|'
    r'ecore_main_loop_(thread_safe|select_func|begin|iterate)|ecore_event_(handler_add|add)|ecore_main_fd_handler_add|'
    r'^pm_(lock_state|set_trigger)$')

def read_at(elf,address,size):
    for seg in elf.iter_segments():
        if seg['p_type']=='PT_LOAD' and seg['p_vaddr']<=address and address+size<=seg['p_vaddr']+seg['p_filesz']:
            return seg.data()[address-seg['p_vaddr']:address-seg['p_vaddr']+size]
    return b''

def extract_contracts(stock, report):
    """Resolve reviewed indirect dispatches; this never loads or patches an ELF."""
    def open_image(relative):
        return ELFFile(io.BytesIO((stock/relative).read_bytes()))
    app=open_image('usr/bin/di-camera-app-nx300')
    ecore=open_image('usr/lib/libecore.so.1.2.0')
    pm=open_image('usr/lib/libpmapi.so.1.2')
    def word(elf, address):
        return struct.unpack('<I',read_at(elf,address,4))[0]
    def string(elf, address):
        value=bytearray()
        for offset in range(256):
            byte=read_at(elf,address+offset,1)
            if not byte or byte==b'\0':break
            value.extend(byte)
        return value.decode('utf-8',errors='replace')
    def branch(elf, address):
        value=word(elf,address)
        if value&0x0f000000!=0x0b000000:
            raise ValueError(f'Expected A32 BL at {address:#x}')
        displacement=value&0xffffff
        if displacement&0x800000:displacement-=0x1000000
        return (address+8+displacement*4)&0xffffffff
    # PIC bases use the address of the ADD instruction plus A32 PC bias 8.
    set_slot=(0xdb40+word(ecore,0xdb44)+word(ecore,0xdb48))&0xffffffff
    get_slot=(0xdb5c+word(ecore,0xdb60)+word(ecore,0xdb64))&0xffffffff
    loop_slot=(0xc8b0+word(ecore,0xcf68)+word(ecore,0xcf78))&0xffffffff
    if {set_slot,get_slot,loop_slot}!={0x21bb4}:
        raise ValueError('Reviewed Ecore select slot does not match')
    relocation=[]
    for section in ecore.iter_sections():
        if section['sh_type']!='SHT_REL':continue
        symbols=ecore.get_section(section['sh_link'])
        for item in section.iter_relocations():
            if item['r_offset']==set_slot:
                relocation.append({'section':section.name,'type':item['r_info_type'],
                                   'symbol':symbols.get_symbol(item['r_info_sym']).name})
    if not any(item['symbol']=='select' and item['type']==2 for item in relocation):
        raise ValueError('Expected R_ARM_ABS32 select relocation')
    if word(app,0x26a6e4)!=0x17bc98 or word(app,0x2615c0)!=0x17c748:
        raise ValueError('Reviewed liveview key dispatch differs')
    if branch(app,0x17d47c)!=0x17c748:
        raise ValueError('Custom exported wrapper target differs')
    calls={hex(address):hex(branch(ecore,address))
           for address in (0xd5c4,0xd648,0xd6cc,0xd728)}
    if set(calls.values())!={'0xc88c'}:
        raise ValueError('Reviewed main loop wait calls differ')
    decoder=Cs(CS_ARCH_ARM,CS_MODE_ARM);decoder.skipdata=True
    for filename,elf,ranges in (
        ('ecore-select-contract.asm',ecore,[(0xdb30,56),(0xca58,0x44),(0xcf68,20),
                                           (0xd5c0,12),(0xd63c,20),(0xd6c8,16),(0xd724,12)]),
        ('key-context-contract.asm',app,[(0x1a5d40,0x28),(0x17bc98,96),
                                        (0x17c748,0x1dc),(0x17d468,32)])):
        lines=[]
        for address,size in ranges:
            lines.append(f'\n## {address:#x} size={size:#x}')
            lines.extend(f'{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}'
                         for ins in decoder.disasm(read_at(elf,address,size),address))
        (OUT/filename).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    pm_base=(0x780+word(pm,0x81c))&0xffffffff
    pm_socket=(pm_base+word(pm,0x820))&0xffffffff
    contracts={
        'scope':'NX300M 1.15 static ABI and indirect dispatch evidence; no target execution',
        'target_executed':False,'firmware_or_card_written':False,
        'source_identity':[{key:item[key] for key in ('path','bytes','sha256','elf_type')}
                           for item in report['images']],
        'ecore_select':{'library':'usr/lib/libecore.so.1.2.0','slot_elf_va':hex(set_slot),
            'set':hex(0xdb30),'get':hex(0xdb4c),'initial_file_word':hex(word(ecore,set_slot)),
            'relocations':relocation,'wait_function':'0xc88c','indirect_call':'0xca8c',
            'main_loop_wait_calls':calls,'runtime_bootstrap_validated':False,
            'preserve':'current runtime callback, including a possible glib integration wrapper'},
        'key_dispatch':{'library':'usr/bin/di-camera-app-nx300','state_table':'0x26a6e0',
            'state_source':'UI_Get_State(2)','event_key_offset':4,'event_action_offset':8,
            'liveview_state':1,'liveview_dispatch':'0x17bc98','liveview_key_table':'0x261590',
            'liveview_key_filter':'0x17d128','custom_key_index':12,'custom_slot':'0x2615c0',
            'custom_actual_handler':'0x17c748','exported_wrapper':'0x17d468',
            'playback_state_handlers':{str(state):hex(word(app,0x26a6e0+state*4))
                                       for state in (18,19,20,21)},
            'runtime_hook_validated':False},
        'power':{'timer_control':'0x1d0f88','bool_true':'stop timers 0x28, 0x29, 0x2c',
            'bool_false':'restart timers subject to stock preference and mode gates',
            'timer_callback_table':'0x2604f4','timer_handles':'0x4dbb94',
            'timer_callbacks':{hex(timer):hex(word(app,0x2604f4+timer*4))
                               for timer in (0x28,0x29,0x2c)},
            'callback_log_names':{'0x28':string(app,0x260654),'0x29':string(app,0x26061c)},
            'pm_transport':{'family':'AF_UNIX','type':'SOCK_DGRAM','socket':string(pm,pm_socket),
                            'bytes':20,'returns_ack':False}},
        'ae_program_line':{'library':'usr/lib/libiq-fw-slpcam.so',
            'math':'0x2ce900','math_endpoint_count':{'A':2,'P':5},
            'endpoint_promoted_ints':['Av16','Tv16','Sv16','exposure_bias16'],
            'auto_iso_shift':'0x3018a0','capture_dispatch':'0x30303c',
            'capture_modes':{'P':0,'A':1,'S':2,'M':3},
            'capture_slow_limit_constant':-80,'slow_limit_assignment':'0x2fa54c',
            'program_line_object_offsets':{'auto_iso':hex(0xf5c),'Sv_base':hex(0xf5e),
                                          'Tv_max':hex(0xf64),'Tv_min':hex(0xf66)},
            'complete_AP_range_control_implemented':False}}
    return contracts

def inspect(path,relative,names,symbols_only):
    data=path.read_bytes();elf=ELFFile(io.BytesIO(data));dynamic=list(elf.get_section_by_name('.dynsym').iter_symbols())
    symbols={s.name:s for s in dynamic}
    static=elf.get_section_by_name('.symtab')
    if static:
        for s in static.iter_symbols():symbols.setdefault(s.name,s)
    selected=[s for s in symbols.values() if (any(n in s.name for n in names) if names else PATTERNS.search(s.name))]
    result={'path':relative,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'elf_type':elf['e_type'],'symbols':[]}
    functions=sorted((s['st_value']&~1,s['st_size'],s.name) for s in symbols.values()
        if s['st_info']['type']=='STT_FUNC' and s['st_shndx']!='SHN_UNDEF' and s['st_size'])
    starts=[f[0] for f in functions]
    target_names={s['st_value']&~1:s.name for s in symbols.values() if s['st_shndx']!='SHN_UNDEF' and s['st_value']}
    plt,rel=elf.get_section_by_name('.plt'),elf.get_section_by_name('.rel.plt')
    if plt and rel:
        for i,r in enumerate(rel.iter_relocations()):target_names[plt['sh_addr']+20+i*12]=dynamic[r['r_info_sym']].name+'@plt'
    selected_names={s.name for s in selected}
    materializations={}
    if not symbols_only:
        # A32 GCC often constructs callback addresses with MOVW/MOVT rather
        # than a literal pool. Record those sites without treating them as calls.
        section=elf.get_section_by_name('.text');code=section.data();base=section['sh_addr']
        for off in range(0,len(code)-3,4):
            first=struct.unpack_from('<I',code,off)[0]
            if first&0x0ff00000!=0x03000000:continue
            reg=(first>>12)&15;low=((first>>16)&15)<<12|(first&4095)
            for step in range(4,20,4):
                if off+step+4>len(code):break
                second=struct.unpack_from('<I',code,off+step)[0]
                if second&0x0ff0f000!=(0x03400000|(reg<<12)):continue
                high=((second>>16)&15)<<12|(second&4095);value=low|(high<<16)
                address=base+off;idx=bisect_right(starts,address)-1
                owner=functions[idx][2] if idx>=0 and address<functions[idx][0]+functions[idx][1] else None
                materializations.setdefault(value,[]).append({'movw':hex(address),'movt':hex(address+step),'register':reg,'owner':owner})
                break
    callers={name:[] for name in selected_names}
    if not symbols_only:
        # Decode per symbol, including Thumb functions. B edges outside the
        # owner are retained as tail-call candidates rather than lost as BL-only.
        for address,size,name in functions:
            raw=read_at(elf,address,size)
            if not raw:continue
            s=symbols[name];decoder=Cs(CS_ARCH_ARM,CS_MODE_THUMB if s['st_value']&1 else CS_MODE_ARM)
            decoder.detail=True;decoder.skipdata=True
            for ins in decoder.disasm(raw,address):
                if ins.mnemonic not in ('bl','blx','b','b.w') or not ins.operands or ins.operands[0].type!=ARM_OP_IMM:continue
                dest=ins.operands[0].imm&0xffffffff;target=target_names.get(dest,'').removesuffix('@plt')
                if target not in selected_names or (ins.mnemonic in ('b','b.w') and address<=dest<address+size):continue
                callers[target].append({'address':hex(ins.address),'owner':name,'instruction':ins.mnemonic,'target':hex(dest)})
    text=[]
    for s in sorted(selected,key=lambda s:(s['st_value'],s.name)):
        address,size=s['st_value']&~1,s['st_size'];defined=s['st_shndx']!='SHN_UNDEF'
        entry={'name':s.name,'address':hex(address),'size':size,'type':s['st_info']['type'],'defined':defined}
        if not symbols_only:entry['callers']=callers[s.name]
        if not symbols_only:entry['address_materializations']=materializations.get(s['st_value'],[])
        if defined and s['st_info']['type']=='STT_FUNC' and not symbols_only:
            references=[]
            for sec in elf.iter_sections():
                if not sec['sh_flags']&2 or sec['sh_flags']&4 or sec['sh_type']!='SHT_PROGBITS' or not sec.name.startswith(('.data','.rodata','.got')):continue
                content=sec.data();needle=struct.pack('<I',s['st_value']);offset=content.find(needle)
                while offset>=0:
                    if (sec['sh_addr']+offset)%4==0:
                        words=[]
                        for pos in range(max(0,offset-12),min(len(content)-3,offset+16),4):
                            value=struct.unpack_from('<I',content,pos)[0]
                            words.append({'address':hex(sec['sh_addr']+pos),'value':hex(value),'symbol':target_names.get(value&~1)})
                        references.append({'address':hex(sec['sh_addr']+offset),'section':sec.name,'nearby_words':words})
                    offset=content.find(needle,offset+1)
            entry['data_references']=references
        result['symbols'].append(entry)
        text.append(f'\n## {s.name} {address:#x} {size} defined={defined}')
        if not defined or s['st_info']['type']!='STT_FUNC' or symbols_only:continue
        raw=read_at(elf,address,size);entry['code_sha256']=hashlib.sha256(raw).hexdigest()
        decoder=Cs(CS_ARCH_ARM,CS_MODE_THUMB if s['st_value']&1 else CS_MODE_ARM)
        decoder.detail=True;decoder.skipdata=True
        for ins in decoder.disasm(raw,address):
            note=[]
            if ins.id:
                if ins.mnemonic in ('b','b.w','bl','blx') and ins.operands and ins.operands[0].type==ARM_OP_IMM:
                    target=target_names.get(ins.operands[0].imm&0xffffffff)
                    if target:note.append(target)
                for op in ins.operands:
                    if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                        loc=((ins.address+4)&~3 if s['st_value']&1 else ins.address+8)+op.mem.disp
                        value=read_at(elf,loc,4)
                        if len(value)==4:note.append(f'literal[{loc:#x}]={int.from_bytes(value,"little"):#x}')
            text.append(f'{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}'+(' ; '+'; '.join(note) if note else ''))
    return result,'\n'.join(text)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbols-only',action='store_true');p.add_argument('--name',action='append',default=[])
    p.add_argument('--library',action='append');args=p.parse_args()
    stock=Path(json.loads((ROOT/'project.local.json').read_text())['rootfs']);OUT.mkdir(parents=True,exist_ok=True)
    libraries=args.library or ['usr/bin/di-camera-app-nx300','usr/lib/libcapture-fw-slpcam-nx300.so',
        'usr/lib/libiq-fw-slpcam.so','usr/lib/liblens-fw-prod.so','usr/lib/libecore.so.1.2.0','usr/lib/libpmapi.so.1.2']
    report={'scope':'static stock ELF analysis; direct edges are candidates until body review',
            'target_executed':False,'firmware_or_card_written':False,'images':[]}
    for relative in libraries:
        record,text=inspect(stock/relative,relative,args.name,args.symbols_only)
        report['images'].append(record)
        (OUT/(Path(relative).name+('.symbols.txt' if args.symbols_only else '.asm'))).write_text(text,encoding='utf-8')
    target=OUT/('symbols.json' if args.symbols_only else 'interfaces.json')
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not args.symbols_only and not args.library and not args.name:
        contracts=extract_contracts(stock,report)
        (OUT/'contracts.json').write_text(json.dumps(contracts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(target),'images':[{ 'path':i['path'],'matches':len(i['symbols'])} for i in report['images']]},ensure_ascii=False))

if __name__=='__main__':main()
