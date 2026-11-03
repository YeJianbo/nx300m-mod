"""Extract stock NX300M 1.15 Picture Wizard and SmartFilter selectors.

Only file-backed ELF data and direct ARM branches are inspected. The report
keeps parameter selection separate from hardware pixel ordering.
"""
import hashlib
import json
from pathlib import Path
import struct

from inspect_gamma_hook import Stock, sym
from inspect_mode_enums import Image, enum_groups, converted_value
from inspect_smartfilter_pipeline import Graph, LIBRARIES, IQ, CAPTURE

ROOT = Path(__file__).resolve().parents[1]
SMART = 'libdi-alg-smartfilter.so'
DISPATCH = '_Z26ADE_SmartFilter_YccProcess9eADE_MODEP9ADE_Image'


def instructions(stock, name):
    entry = stock.symbols[name]
    return list(stock.decoder.disasm(stock.read(entry['st_value'], entry['st_size']),
                                     entry['st_value']))


def relative_table(stock, add_address, base_pool, offset_pool, count):
    base = (add_address + 8 + stock.word(base_pool)) & 0xffffffff
    address = (base + stock.word(offset_pool)) & 0xffffffff
    return {'address': hex(address), 'values': list(struct.unpack(
        '<' + str(count) + 'i', stock.read(address, count * 4))),
        'evidence': {'pic_add': hex(add_address), 'base_literal': hex(base_pool),
                     'table_offset_literal': hex(offset_pool)}}


def jump_targets(stock, address, count):
    result = []
    for i in range(count):
        ins = stock.instruction(address + i * 4)
        if ins.mnemonic != 'b':
            raise ValueError(f'Expected branch table at {ins.address:#x}')
        result.append(int(ins.op_str[1:], 0))
    return result


def write_assembly(stocks, graph, selected, path):
    parts = []
    for library, names in selected.items():
        stock = stocks[library]
        for name in dict.fromkeys(names):
            entry = stock.symbols[name]
            calls = {e['call']: e.get('symbol', 'indirect ' + e.get('indirect', ''))
                     for e in graph.scan((library, entry['st_value']))}
            parts.append(f'\n## {library} {name} size={entry["st_size"]}\n')
            for ins in instructions(stock, name):
                parts.append(f'{ins.address:08x} {ins.bytes.hex():8s} '
                             f'{ins.mnemonic:8s} {ins.op_str} '
                             f'{calls.get(hex(ins.address), "")}\n')
    path.write_text(''.join(parts), encoding='utf-8')


def inspect(rootfs):
    stocks = {lib: Stock(rootfs / 'usr/lib' / lib) for lib in LIBRARIES}
    graph = Graph(stocks)
    iq, sf, capture = (stocks[n] for n in (IQ, SMART, CAPTURE))
    _, groups = enum_groups(Image(rootfs / 'usr/lib' / CAPTURE))
    groups = {g['name']: g for g in groups}
    image = Image(rootfs / 'usr/lib' / CAPTURE)
    pss = relative_table(iq, 0x350224, 0x350270, 0x350274, 22)
    ccm = relative_table(iq, 0x33dfc8, 0x33e0b0, 0x33e0b4, 12)
    tone = relative_table(iq, 0x33c6e0, 0x33c86c, 0x33c870, 12)
    preview_tone = relative_table(iq, 0x340b60, 0x340c58, 0x340c5c, 12)
    tables = {}
    for cls in ('CIpcsColorD4', 'CIpcmColorD4'):
        for field in ('m_iPssMatrix', 'm_iColorTone'):
            tables[cls + '.' + field] = iq.table(sym(cls, field), 9)
    for cls in ('CIpcsGammaD4', 'CIpcmGammaD4'):
        for field in ('m_iEffectToneX', 'm_iEffectToneY', 'm_iContrastX', 'm_iContrastY'):
            tables[cls + '.' + field] = iq.table(sym(cls, field), 7)
    rows = []
    for label in groups['PW']['labels']:
        raw = (groups['PW']['userdata_id'] << 16) | label['ordinal']
        iq_value, trace = converted_value(image, raw)
        pss_value = pss['values'][iq_value]
        matrix_index = ccm['values'][pss_value - 1] if 1 <= pss_value <= 7 else 0
        effect_index = tone['values'][pss_value - 1] if 1 <= pss_value <= 12 else 0
        preview_index = (preview_tone['values'][pss_value - 1]
                         if 1 <= pss_value <= 12 else 0)
        rows.append({'name': label['label'], 'userdata_raw': hex(raw),
                     'iq_0x2c': iq_value, 'conversion_trace': trace,
                     'pss': pss_value, 'pss_matrix_row': matrix_index,
                     'still_effect_tone_row': effect_index,
                     'preview_effect_tone_row': preview_index,
                     'matrix_q8': tables['CIpcsColorD4.m_iPssMatrix']['rows'][matrix_index],
                     'still_tone_y': tables['CIpcsGammaD4.m_iEffectToneY']['rows'][effect_index],
                     'preview_tone_y': tables['CIpcmGammaD4.m_iEffectToneY']['rows'][preview_index]})

    # The name function creates fourteen consecutive 16-byte stack slots from
    # distinct file-backed string literals. Resolve their actual PIC references.
    name_function = '_Z17ADE_GetFilterName9eADE_MODEPc'
    name_ins = instructions(sf, name_function)
    base = (0x2d4c + 8 + sf.word(0x3278)) & 0xffffffff
    names = []
    for ins, following in zip(name_ins, name_ins[1:]):
        if (ins.mnemonic == 'ldr' and ins.op_str.startswith('r3, [pc,') and
                following.mnemonic == 'add' and
                following.op_str in ('r2, r4, r3', 'r3, r4, r3')):
            address = (base + sf.literal(ins.address)[1]) & 0xffffffff
            names.append({'mode': len(names), 'name': sf.read(address, 16).split(b'\0')[0].decode(),
                          'literal_load': hex(ins.address), 'string_address': hex(address)})
    if len(names) != 14:
        raise ValueError('Changed stock filter-name layout')
    dispatch_ins = instructions(sf, DISPATCH)
    dispatch_calls = {int(e['call'], 16): e for e in graph.scan(
        (SMART, sf.symbols[DISPATCH]['st_value']))}
    blocks = jump_targets(sf, 0x2ac4, 13)
    algorithms = []
    for mode, start in enumerate(blocks, 1):
        edge = next(dispatch_calls[ins.address] for ins in dispatch_ins
                    if start <= ins.address < start + 16 and ins.address in dispatch_calls)
        name = edge['symbol']
        calls = graph.scan((SMART, int(edge['address'], 16)))
        body = instructions(sf, name)
        stub = (len(body) == 9 and not calls and
                [(i.mnemonic, i.op_str) for i in body[4:6]] ==
                [('mvn', 'r3, #0'), ('mov', 'r0, r3')])
        algorithms.append({**names[mode], 'case_target': hex(start), 'dispatch': edge,
                           'body_status': 'constant return -1' if stub else 'processing body',
                           'ordered_static_calls': calls})
    processor_names = sorted({e['symbol'] for a in algorithms for e in a['ordered_static_calls']
                              if e.get('library') == 'libdi-alg-ipplatform.so' and
                              ('MnFparam_SET' in e.get('symbol', '') or
                               'ProcessLut' in e.get('symbol', ''))})
    processors = [{'symbol': name,
                   'address': hex(stocks['libdi-alg-ipplatform.so'].symbols[name]['st_value']),
                   'calls': graph.scan(('libdi-alg-ipplatform.so',
                                       stocks['libdi-alg-ipplatform.so'].symbols[name]['st_value']))}
                  for name in processor_names]

    # UI SmartFilter indices are a different enum from ADE and from live view.
    lv_targets = jump_targets(capture, 0x26f6c8, 22)
    ui_rows = []
    for row in groups['SMARTFILTERTYPE']['labels']:
        raw_index = row['ordinal']
        branch = lv_targets[raw_index - 1] if raw_index else 0x26f720
        move = capture.instruction(branch)
        if move.mnemonic != 'mov' or not move.op_str.startswith('r3, #'):
            raise ValueError('Changed GetSmartFilterMode block')
        lv_mode = int(move.op_str.split('#')[1], 0)
        ui_rows.append({'ui_name': row['label'], 'ui_index': raw_index,
                        'liveview_enum': lv_mode, 'selection_block': hex(branch)})
    lv_to_ade = []
    for mode, branch in enumerate(jump_targets(capture, 0x27d1d0, 10)):
        move = capture.instruction(branch)
        if move.mnemonic != 'mov' or not move.op_str.startswith('r3, #'):
            raise ValueError('Changed preview ADE mapping')
        ade = int(move.op_str.split('#')[1], 0)
        lv_to_ade.append({'liveview_enum': mode, 'ade_mode': ade,
                          'name': names[ade]['name'], 'selection_block': hex(branch)})
    for row in ui_rows:
        if row['liveview_enum'] < len(lv_to_ade):
            row['ade_mode'] = lv_to_ade[row['liveview_enum']]['ade_mode']
            row['ade_name'] = names[row['ade_mode']]['name']

    # Still's UI switch has additional non-ADE cases. Keep its per-case branch
    # evidence, including selective-colour and other handlers, instead of
    # assuming that the preview mapping covers it.
    still_name = '_ZN14CEffectProcess18processSmartFilterEv'
    still_ins = instructions(capture, still_name)
    switch = next(i for i in still_ins if 0x2a8158 < i.address < 0x2a8178
                  and i.mnemonic == 'addls' and i.op_str.startswith('pc, pc,'))
    count_ins = capture.instruction(switch.address - 4)
    count = int(count_ins.op_str.split('#')[1], 0) + 1
    still_targets = jump_targets(capture, switch.address + 8, count)
    still_calls = {int(e['call'], 16): e for e in graph.scan(
        (CAPTURE, capture.symbols[still_name]['st_value']))}
    still_cases = []
    for index, branch in enumerate(still_targets, 1):
        row = {'ui_index': index}
        row['still_case_target'] = hex(branch)
        last_r0 = None
        for address in range(branch, 0x2a86dc, 4):
            ins = capture.instruction(address)
            if ins.mnemonic == 'b':
                break
            if ins.mnemonic == 'mov' and ins.op_str.startswith('r0, #'):
                last_r0 = int(ins.op_str.split('#')[1], 0)
            edge = still_calls.get(address)
            if edge and edge.get('symbol') == DISPATCH:
                row.update(still_ade_mode=last_r0, still_call=hex(address),
                           still_ade_name=names[last_r0]['name'])
                break
            if ins.mnemonic in ('bl', 'blx'):
                last_r0 = None
        still_cases.append(row)
    for row in ui_rows:
        if row['ui_index']:
            row.update(still_cases[row['ui_index'] - 1])

    selected = {
        IQ: ['_ZN10CIpcIfInfo6GetPssE13ePROCESS_MODEP12tagRGBTRIPLE',
             '_ZN10CIpcIfInfo12GetColorToneEv', '_ZN10CIpcIfInfo13GetSaturationEv',
             '_ZN10CIpcIfInfo11GetContrastEv', '_ZN10CIpcIfInfo8Get3DLutEv',
             '_ZN16CIpcStillParamD410GetSysInfoEv', '_ZN19CIpcLiveviewParamD410GetSysInfoEv',
             '_ZN12CIpcsColorD415GetRgb2RgbIndexEPvRj', '_ZN12CIpcsColorD410SetRgb2RgbEi',
             '_ZN12CIpcmColorD415GetRgb2RgbIndexEPvRj', '_ZN12CIpcmColorD410SetRgb2RgbEi',
             '_ZN12CIpcsGammaD48GetIndexEPv', '_ZN12CIpcmGammaD48GetIndexEPv',
             '_ZN12CIpcsGammaD48SetParamEj', '_ZN12CIpcmGammaD48SetParamEj',
             '_ZN12CIpcs3DLutD48GetIndexEPv', '_ZN12CIpcs3DLutD48SetParamEj',
             '_ZN13CColorProcess20CalculateColorMatrixEv'],
        SMART: [DISPATCH, name_function, '_Z28ADE_Foggy_MistRemove_MakeLUTjjPt'] +
               [a['dispatch']['symbol'] for a in algorithms],
        CAPTURE: ['_ZN17CProcessParameter16updateEffectModeEv',
                  '_ZN15CLiveviewCommon18GetSmartFilterModeEv',
                  '_ZN24CLiveviewNodeSmartFilter11smartFilterEP9CCapImagePv22E_LIVEVIEW_SMARTFILTER',
                  still_name],
        'libdi-alg-ipplatform.so': processor_names,
    }
    report = {
        'method': 'static original ARM branches, PIC tables and ELF symbols',
        'firmware': 'NX300M 1.15',
        'sources': {n: {'sha256': hashlib.sha256(s.path.read_bytes()).hexdigest(),
                        'bytes': s.path.stat().st_size} for n, s in stocks.items()},
        'picture_wizard': {
            'presets': rows, 'selector_tables': {'iq_to_pss': pss, 'pss_to_ccm': ccm,
                                                'pss_to_still_tone': tone,
                                                'pss_to_preview_tone': preview_tone},
            'native_tables': tables,
            'scope': 'Preset selectors in normal PW mode; scene/lens-effect overrides and '
                     'saved per-preset saturation, contrast and colour settings are additional inputs.',
            'matrix_expression': 'ColorTone * (PSS * current_base / 256) / 256; '
                                 'each matrix product uses integer division toward zero',
            'base_matrix_generation': graph.scan((IQ, 0x334e28)),
            'still_3d_selection': {'getter': 'CIpcIfInfo::Get3DLut @0x350ad8',
                'ae_request': 'IQ_AE_GetAeValue(output,0x18) @0x350ae8',
                'branch': 'result <= 160 -> 1; otherwise 0 @0x350af0-0x350af8',
                'pw_preset_not_read_by_this_getter': True,
                'ae_quantity_and_unit': 'not identified by this extraction'},
            'pixel_order_proven': False,
        },
        'smartfilter': {'ui_to_handlers': ui_rows, 'liveview_to_ade': lv_to_ade,
                        'still_dispatch_cases': still_cases,
                        'ade_names': names, 'ade_dispatch': algorithms,
                        'platform_processors': processors,
                        'ordered_calls_are_pixel_order': False,
                        'spatial_algorithms_are_not_pointwise_3d_luts': True},
        'remaining': ['Active per-preset user saturation/contrast/colour values and reset defaults',
                      'All IQ scene/lens/adaptive overrides and their mode coverage',
                      'Hardware pixel order, transfer functions, primaries and clipping locations',
                      'MnF kernel internals, all selective-colour branches and indirect calls',
                      'Real sensor/JPEG pixel validation of native colour model'],
    }
    return report, stocks, graph, selected


def main():
    config = json.loads((ROOT / 'project.local.json').read_text(encoding='utf-8-sig'))
    report, stocks, graph, selected = inspect(Path(config['rootfs']))
    output = ROOT / 'outputs/metol/native-style-pipeline'
    output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                           encoding='utf-8')
    write_assembly(stocks, graph, selected, output.with_suffix('.asm'))
    print(json.dumps({'picture_wizard': [{k: row[k] for k in
                       ('name', 'iq_0x2c', 'pss', 'pss_matrix_row', 'still_effect_tone_row')}
                       for row in report['picture_wizard']['presets']],
                      'smartfilter_cases': len(report['smartfilter']['ui_to_handlers']),
                      'platform_processors': len(report['smartfilter']['platform_processors']),
                      'output': str(output.with_suffix('.json'))}, ensure_ascii=False))


if __name__ == '__main__':
    main()
