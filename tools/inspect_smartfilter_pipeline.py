"""Locate original NX300M frame, ADE YCC and shared IPCS control paths.

This is a read-only ELF inspection. Branches describe possible execution;
the separate isolated probe is responsible for exercising selected branches.
"""
import argparse
import collections
import hashlib
import io
import json
from pathlib import Path
import re

from elftools.elf.elffile import ELFFile

from inspect_gamma_hook import Stock
from inspect_recipe_tables import disassemble

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = 'libcapture-fw-slpcam-nx300.so'
PLATFORM = 'libdi-alg-ipplatform.so'
PHOTO = 'libdi-alg-photoeditor.so'
UDD = 'libudd.so.3.1'
IQ = 'libiq-fw-slpcam.so'
STILL_IQ_CALLBACK = '_ZN11CIpcProcess30SetIpcsIQParamCallBackFunctionEv'
LIBRARIES = (CAPTURE, PLATFORM, PHOTO, UDD, IQ,
             'libdi-alg-smartfilter.so', 'libdi-alg-selectivecolor.so')
ADE_YCC = '_Z30ADE_Ipcs_Ycc_correct_Param_SETP9ADE_Image'
YCC_OPERATION = 'ipcs_ycc_correction_to_ycc_operation'


def rootfs_path():
    return Path(json.loads((ROOT/'project.local.json').read_text(
        encoding='utf-8-sig'))['rootfs'])


class Graph:
    def __init__(self, stocks):
        self.stocks = stocks
        self.functions, self.exports, self.plts = {}, collections.defaultdict(list), {}
        self.edges = {}
        for library, stock in stocks.items():
            for name, sym in stock.symbols.items():
                if sym['st_info']['type'] != 'STT_FUNC' or not sym['st_size']:
                    continue
                key = (library, int(sym['st_value']) & ~1)
                fn = self.functions.setdefault(key, {
                    'library': library, 'address': hex(key[1]),
                    'size': int(sym['st_size']), 'symbol': name, 'aliases': []})
                fn['aliases'].append(name)
                self.exports[name].append(key)
            plt = stock.elf.get_section_by_name('.plt')
            rel = stock.elf.get_section_by_name('.rel.plt')
            self.plts[library] = {}
            if plt is not None and rel is not None:
                dyn = stock.elf.get_section(rel['sh_link'])
                self.plts[library] = {
                    int(plt['sh_addr'])+20+i*12: dyn.get_symbol(r['r_info_sym']).name
                    for i, r in enumerate(rel.iter_relocations())}

    def scan(self, key):
        if key in self.edges:
            return self.edges[key]
        library, address = key
        stock, fn = self.stocks[library], self.functions[key]
        result = []
        for ins in stock.decoder.disasm(stock.read(address, fn['size']), address):
            if ins.mnemonic not in ('bl', 'blx', 'b'):
                continue
            if not ins.op_str.startswith('#'):
                if ins.mnemonic != 'b':
                    result.append({'call': hex(ins.address), 'indirect': ins.op_str})
                continue
            target = int(ins.op_str[1:], 0)
            if ins.mnemonic == 'b' and address <= target < address+fn['size']:
                continue
            name = self.plts[library].get(target)
            destination = None
            if name:
                owners = self.exports.get(name, [])
                destination = next((v for v in owners if v[0] == library),
                                   owners[0] if owners else None)
            elif (library, target) in self.functions:
                destination = (library, target)
                name = self.functions[destination]['symbol']
            if name:
                result.append({'call': hex(ins.address), 'symbol': name,
                               'library': destination[0] if destination else None,
                               'address': hex(destination[1]) if destination else None})
        self.edges[key] = result
        return result

    def callers(self, name):
        return [{'caller': self.functions[key], 'edge': edge}
                for key in self.functions for edge in self.scan(key)
                if edge.get('symbol') == name]

    def path(self, source, target, max_depth=12):
        queue = collections.deque((key, []) for key in self.exports[source])
        seen = set()
        while queue:
            key, path = queue.popleft()
            if key in seen or len(path) >= max_depth:
                continue
            seen.add(key)
            for edge in self.scan(key):
                if 'symbol' not in edge:
                    continue
                step = {'from': self.functions[key]['symbol'],
                        'from_library': key[0], **edge}
                if edge['symbol'] == target:
                    return path+[step]
                if edge['library']:
                    queue.append(((edge['library'], int(edge['address'], 16)), path+[step]))
        return None


def imports(rootfs):
    targets = {ADE_YCC, YCC_OPERATION, 'ipcs_open', 'ipcs_close'}
    count, references, errors = 0, [], []
    paths = list((rootfs/'usr/lib').glob('*.so*'))
    paths.append(rootfs/'usr/bin/di-camera-app-nx300')
    for path in sorted(paths):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            raw = path.read_bytes()
            if raw[:4] != b'\x7fELF':
                continue
            elf = ELFFile(io.BytesIO(raw))
            dyn = elf.get_section_by_name('.dynsym')
            if dyn is None:
                continue
            count += 1
            matches = [{'symbol': s.name, 'defined': s['st_shndx'] != 'SHN_UNDEF',
                        'address': hex(s['st_value'])}
                       for s in dyn.iter_symbols() if s.name in targets]
            if matches:
                references.append({'file': path.name, 'symbols': matches})
        except (OSError, ValueError) as error:
            errors.append({'file': path.name, 'error': str(error)})
    return {'elf_count': count, 'references': references, 'errors': errors,
            'scope': 'usr/lib non-symlink ELF files and di-camera-app-nx300 dynamic symbols; dlsym and indirect calls are not resolved'}


def vtable(stock, name):
    symbol = stock.symbols[name]
    base, size = int(symbol['st_value']), int(symbol['st_size'])
    return {'symbol': name, 'address': hex(base), 'bytes': size,
            'entries': [{'offset': hex(address-base), 'address_point_offset': hex(address-base-8),
                         'relocation': rel}
                        for address, rel in sorted(stock.relocs.items())
                        if base <= address < base+size]}


def pic_binding(stock, load, add, offset_load, expected):
    base = (add+8+stock.literal(load)[1]) & 0xffffffff
    slot = (base+stock.literal(offset_load)[1]) & 0xffffffff
    relocation = stock.relocs[slot]
    if relocation['symbol'] != expected or relocation['type'] != 21:
        raise ValueError(f'Unexpected GOT binding at {slot:#x}: {relocation}')
    return {'pic_load': hex(load), 'pic_add': hex(add),
            'offset_load': hex(offset_load), 'got_slot': hex(slot),
            'relocation': relocation}


def selected(library, name):
    if library == CAPTURE:
        return any(t in name for t in (
            'CLiveviewPathListBase', 'CLiveviewPathListLiveview', 'CLiveviewPathTable',
            'CLiveviewNodeSmartFilter', 'CLiveviewCallback', 'CLiveviewBuffer',
            'CLiveviewNotification', '_ZN9CLiveview',
            'CVideoBuffLock', 'CVideoNodeStatus', 'CDDWrapperIPCS_Resize',
            'CDDWrapperIPCS_B2Y',
            'CLiveviewCommon18GetSmartFilterMode',
            'CLiveviewCommon18SetSmartFilterMode', 'CLiveviewNode9BringDown',
            'CFrontEndInterface14videoStreamOff',
            'CFrontEndMsgHandler14messageHandler',
            'CFrontEndMsgHandler14videoStreamOff',
            'CFrontEndMsgHandler12stillCapture',
            'CFrontEndMsgHandler17stillCaptureBayer',
            'CVideoEventHandler9stopVideo', 'CVideoEventHandler12stopSubBlock',
            'CVideoEventHandler12eventHandler', 'CVideoEventHandler12eventAnalyze',
            'CVideoEventHandler6t_Main', 'CDriveBase12stopLiveview',
            'CDriveSingle12stopLiveview', 'CLiveviewStopCompletion',
            'CCpoIpc14IPCS_rawToMain',
            'CIpcProcess20changeParameter2Send',
            'CIpcProcess30SetIpcsIQParamCallBackFunction',
            'CEffectProcess18processSmartFilter',
            'CProcessParameter13GetEffectMode',
            'CMemoryManager_Nx28onItemChange_SmartFilterType'))
    if library == PLATFORM:
        return any(t in name for t in ('ADE_Ipcs', 'ADE_Image', 'ADE_Flush', 'ADE_Cache'))
    if library == PHOTO:
        return name.startswith('_Z') and not name.startswith('_ZNSt')
    if library == UDD:
        return any(t in name for t in ('ycc_correction_to_ycc', 'ipcs_open', 'ipcs_close',
                                      'ipcs_3d_lut', 'ipcs_wait_1tile_done', 'ipcs_submodule_init'))
    if library == IQ:
        return any(t in name for t in ('IQ_IPC_Initialize', 'IQ_IPC_Process',
                                      'CIpcProcessor', 'CIpcStillParamD4',
                                      'CIpcParameterList', 'CIpcParameterNode5Reset'))
    return False


def inspect(scan_imports=True):
    rootfs = rootfs_path()
    stocks = {name: Stock(rootfs/'usr/lib'/name) for name in LIBRARIES}
    graph = Graph(stocks)
    targets = (ADE_YCC, YCC_OPERATION, '_Z13ADE_Ipcs_Openv', 'ipcs_open', 'ipcs_close',
               '_ZN24CLiveviewNodeSmartFilter8TearDownEv',
               '_ZN17CLiveviewCallback13BringDownNodeEv',
               '_ZN17CLiveviewCallback12TearDownNodeEv',
               '_ZN18CVideoEventHandler9stopVideoEv',
               '_ZN18CVideoEventHandler12stopSubBlockEv',
               '_ZN9CLiveview18notifyFromFrontEndEl',
               '_ZN9CLiveview19stopCallbackProcessEmi',
               '_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreE18eIPCS_SEMAPHORE_ID',
               '_ZN25CLiveviewPathListLiveview32setLiveviewNormalPathSmartFilterEiRi')
    callers = {name: graph.callers(name) for name in targets}
    relevant = []
    for key, fn in graph.functions.items():
        if selected(key[0], fn['symbol']):
            relevant.append({**fn, 'calls': graph.scan(key)})
    capture = stocks[CAPTURE]
    vtables = [vtable(capture, name) for name in (
        '_ZTV21CLiveviewPathListBase', '_ZTV25CLiveviewPathListLiveview',
        '_ZTV24CLiveviewNodeSmartFilter', '_ZTV17CLiveviewCallback',
        '_ZTV9CLiveview')]
    bindings = {
        'stream_off_completion': pic_binding(capture, 0x28a408, 0x28a40c,
            0x28a448, '_ZN17CLiveviewCallback16FrontEndCallbackEii'),
        'completion_notification_owner': pic_binding(capture, 0x27b570,
            0x27b574, 0x27b580, '_ZN17CLiveviewCallback10m_InstanceE'),
        'video_stop_wait_semaphore': pic_binding(capture, 0x17d53c,
            0x17d540, 0x17d6bc, 'gSemVideoStopWait'),
        'video_stop_post_semaphore': pic_binding(capture, 0x17d4a4,
            0x17d4a8, 0x17d504, 'gSemVideoStopWait'),
        'still_iq_callback': pic_binding(capture, 0x2a4d4c, 0x2a4d50,
            0x2a4f24, STILL_IQ_CALLBACK)}
    callback_store = capture.instruction(0x2a4f2c)
    if (callback_store.mnemonic, callback_store.op_str) != ('str', 'r2, [r3, #0x3c]'):
        raise ValueError('Unexpected Still IQ callback field assignment')
    callback = capture.symbols[STILL_IQ_CALLBACK]
    callback_key = (CAPTURE, int(callback['st_value']))
    lock_calls = [{'caller': fn, 'calls': [e for e in graph.scan(key)
                   if any(t in e.get('symbol', '') for t in ('semaphore', 'mutex', 'trylock'))]}
                  for key, fn in graph.functions.items()
                  if key[0] in (PLATFORM, UDD, PHOTO) and
                  any(t in fn['symbol'] for t in ('Ipcs', 'ipcs_', 'ADE_AdjustRGB', 'ADE_Exposure'))]
    lock_calls = [item for item in lock_calls if item['calls']]
    listings = []
    for library, stock in stocks.items():
        names = [n for n, s in stock.symbols.items() if s['st_info']['type'] == 'STT_FUNC'
                 and selected(library, n)]
        # Include every discovered caller even when it is outside the classes above.
        names += [item['caller']['symbol'] for group in callers.values() for item in group
                  if item['caller']['library'] == library]
        listing, _ = disassemble(stock.elf, stock.symbols, list(dict.fromkeys(names)))
        listings.append('# '+library+'\n'+listing.replace(
            'disassembly of stock libiq-fw-slpcam.so.', 'disassembly of stock '+library+'.'))
    result = {
        'scope': 'Static NX300M 1.15 ELF call/reference inspection; no camera or firmware writes',
        'libraries': [{'file': name, 'sha256': hashlib.sha256(stock.path.read_bytes()).hexdigest()}
                      for name, stock in stocks.items()],
        'import_scan': imports(rootfs) if scan_imports else None,
        'callers': callers, 'vtables': vtables,
        'iq_vtables': [vtable(stocks[IQ], '_ZTV16CIpcStillParamD4')],
        'functions': relevant,
        'single_slot_stock_state': {
            'liveview_getter': '_ZN15CLiveviewCommon18GetSmartFilterModeEv',
            'liveview_userdata_get_call': '0x26f69c',
            'liveview_setter': '_ZN15CLiveviewCommon18SetSmartFilterModeE22E_LIVEVIEW_SMARTFILTER',
            'liveview_userdata_set_call': '0x26f13c',
            'userdata_key': '0x39',
            'setter_is_global_userdata_write': True,
            'still_effect_process': '_ZN14CEffectProcess18processSmartFilterEv',
            'still_effect_mode_call': '0x2a8150',
            'still_effect_mode_getter': '_ZNK17CProcessParameter13GetEffectModeEv',
            'global_selection_preview_only_established': False,
            'raw_and_mode_ui_restrictions_resolved': False},
        'lifecycle_pic_bindings': bindings,
        'lifecycle_findings': {
            'video_stop_event': '0x800000',
            'video_stop_wait_argument': '0x1388',
            'video_stop_ack': {
                'dispatch_call': '0x178974',
                'handler': '_ZN18CVideoEventHandler12stopSubBlockEv',
                'actions': ['CFrontEndIPCm::stopVideoStream',
                            'CFrontEndIPCm::closeIPCm', 'CFrontEndLDCm::stopLDCm'],
                'semaphore_post_call': '0x17d50c',
                'smartfilter_worker_drain_established': False,
                'ipcs_completion_established': False},
            'front_end_completion': {
                'callback': '_ZN17CLiveviewCallback16FrontEndCallbackEii',
                'notification': '0x14',
                'receiver': '_ZN9CLiveview18onNotifyCompletionEil',
                'dispatch': '_ZN9CLiveview18notifyFromFrontEndEl',
                'stop': '_ZN9CLiveview19stopCallbackProcessEmi'},
            'capture_wrapper_semaphores': {
                'provider': '_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreE18eIPCS_SEMAPHORE_ID',
                'ids': [0, 1, 2, 3],
                'resize_prepare_uses_ids': [1, 2, 3],
                'b2y_prepare_uses_ids': [0, 1, 2, 3],
                'acquisition': 'Conditional on per-wrapper enable fields, ascending ID order',
                'all_ipcs_clients_participate_established': False,
                'still_priority_established': False},
            'still_prepare_callback': {
                'load': '0x190acc', 'operation_parameter_offset': '0x3c',
                'prepare_call': '0x190ad8', 'indirect_callback_call': '0x14e98c',
                'position': 'After conditional semaphore acquisition, before tile operation setup',
                'callback_target_resolved': True,
                'setter': '_ZN11CIpcProcess20changeParameter2SendEP12stIPCS_Paramj',
                'callback_store': hex(callback_store.address),
                'callback_symbol': STILL_IQ_CALLBACK,
                'callback_address': hex(callback['st_value']),
                'callback_direct_calls': graph.scan(callback_key),
                'iq_initialize_call': '0x2a2aa4',
                'iq_process_call': '0x2a2aac',
                'iq_process_mode': 0,
                'initialize_path': graph.path(STILL_IQ_CALLBACK,
                    '_ZN13CIpcProcessor10InitializeE13ePROCESS_MODE'),
                'process_path': graph.path(STILL_IQ_CALLBACK,
                    '_ZN13CIpcProcessor7ProcessE13ePROCESS_MODE'),
                'mode_zero_object': 'CIpcProcessor this+4',
                'mode_zero_initialize_vptr_offset': '0x4',
                'mode_zero_process_vptr_offset': '0x0',
                'mode_zero_vtable_target_resolved': True,
                'mode_zero_type': 'CIpcStillParamD4',
                'mode_zero_initialize_target': '_ZN17CIpcParameterList19InitializeParameterEv',
                'mode_zero_process_target': '_ZN16CIpcStillParamD415UpdateParameterEPv',
                'initialize_list_head_offset': '0x4',
                'initialize_node_next_offset': '0x10',
                'initialize_node_reset_vptr_offset': '0x4',
                'reset_node_cache_index': {'offset': '0x4', 'value': '0xffffffff'},
                'still_color_state_restoration_established': False}},
        'ipcs_platform_udd_photo_lock_calls': lock_calls,
        'smartfilter_to_ade_ycc_direct_path': graph.path(
            '_Z26ADE_SmartFilter_YccProcess9eADE_MODEP9ADE_Image', ADE_YCC),
        'limitations': ['A static branch is not proof that a mode reaches it.',
                        'No lock edge in a function is not proof of global absence of synchronization.',
                        'No emulated or physical ISP pixels are evaluated here.']}
    return result, '\n'.join(listings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-import-scan', action='store_true')
    parser.add_argument('--show', help='Print only matching original functions from the generated listing')
    args = parser.parse_args()
    result, listing = inspect(not args.skip_import_scan)
    out = ROOT/'outputs/metol/smartfilter-pipeline'
    out.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    out.with_suffix('.asm').write_text(listing, encoding='utf-8')
    if args.show:
        for block in listing.split('\n## '):
            if re.search(args.show, block.split('\n', 1)[0]):
                print('## '+block)
    else:
        print(json.dumps({'output': str(out.with_suffix('.json')),
                          'functions': len(result['functions']),
                          'ade_ycc_callers': result['callers'][ADE_YCC],
                          'direct_smartfilter_path': result['smartfilter_to_ade_ycc_direct_path']},
                         ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
