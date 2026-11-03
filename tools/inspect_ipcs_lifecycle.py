"""Read-only NX300M 1.15 IPCS lifetime analysis (UDD ELF + stock kernel).

No firmware code is executed. Kernel internal names are source-correlated;
export names, the d4_ipcs miscdevice/fops and call instructions are independent
binary anchors. Output includes raw instructions so findings can be reviewed.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import statistics
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from inspect_gamma_hook import Stock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/metol/ipcs-lifecycle'
UDD_SHA = '4c2d05bae62f0f4e7399ae97687c7b7c1a025213b4a000ba14003aaa31eb4b26'
KERNEL_SHA = 'ca832c10448db086d73fe9eddda21c1c88f4ff5be10b2fcb41b296518f099cf5'
KBASE = 0xc0008000
FUNCTIONS = (
    'ipcs_open', 'ipcs_close', 'd4_udd_open', 'd4_udd_close',
    'd4_get_udd_open_count', 'ipcs_open_reg_base', 'ipcs_close_reg_base',
    'ipcs_open_irq', 'ipcs_close_irq', 'ipcs_init', 'ipcs_reset',
    'ipcs_sw_dma_reset', 'ipcs_default_regset_load', 'ipcs_cl_smdma_set',
)
# Reviewed ARM ranges in the 2014-12-08 kernel from the v1.15 archive. These
# are not inferred from the older public source layout. Anchors below verify
# the d4_ipcs device and exported targets before using these private labels.
KERNEL_FUNCTIONS = {
    'ipcs_k_dma_rmu_bus': (0xc02a935c, 0xc02a93c8),
    'ipcs_k_clk_set_rate': (0xc02a93c8, 0xc02a93fc),
    'ipcs_pmu_requeset': (0xc02a93fc, 0xc02a9418),
    'ipcs_k_pmu_on_off': (0xc02a9418, 0xc02a95d8),
    'ipcs_k_release': (0xc02a9a10, 0xc02a9a5c),
    'ipcs_k_open': (0xc02a9a8c, 0xc02a9ae4),
    'ipcs_k_get_physical_reg_info': (0xc02a92f4, 0xc02a9310),
    'ipcs_k_interrupt_free': (0xc02a98f0, 0xc02a9904),
    'ipcs_k_interrupt_init': (0xc02a9904, 0xc02a99b8),
    'ipcs_k_mmap': (0xc02a9ae4, 0xc02a9b34),
    'ipcs_k_ioctl': (0xc02a9b34, 0xc02aa644),
    'd4_get_kdd_open_count': (0xc02b0544, 0xc02b0558),
    'd4_kdd_close': (0xc02b060c, 0xc02b0708),
    'd4_kdd_open': (0xc02b070c, 0xc02b07dc),
    'd4_pmu_bus_reset': (0xc019563c, 0xc01956d8),
    'd4_pmu_check': (0xc019581c, 0xc0195860),
    'wait_for_stable': (0xc019592c, 0xc01959ec),
    'pmu_wait_for_ack': (0xc01959f0, 0xc0195ae8),
    '__const_udelay': (0xc01712c4, 0xc01712fc),
    'd4_sw_isp_reset': (0xc0258a68, 0xc0258abc),
    'd4_sw_isp_reset_release': (0xc0258abc, 0xc0258b08),
    'd4_rmu_release': (0xc0258b18, 0xc0258b24),
    'd4_rmu_request': (0xc0258b24, 0xc0258b64),
    'clk_enable': (0xc0019724, 0xc00197fc),
    'clk_disable': (0xc0019680, 0xc0019724),
    'clk_set_rate': (0xc00190cc, 0xc0019180),
    'clk_get': (0xc02d56dc, 0xc02d56f8),
    'clk_get_sys': (0xc02d55d4, 0xc02d56dc),
}


def identity(path):
    raw = path.read_bytes()
    return {'file': str(path), 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}


def direct_branch(address, word):
    """Decode conditional ARM B/BL, and BLX immediate, without decoding data."""
    if word & 0x0e000000 != 0x0a000000:
        return None
    cond = word >> 28
    disp = ((word & 0xffffff) ^ 0x800000) - 0x800000
    target = (address + 8 + disp * 4) & 0xffffffff
    kind = 'bl' if word & 0x01000000 else 'b'
    if cond == 15:
        target += (word >> 23) & 2
        kind = 'blx'
    return target, kind, cond


def plt_names(stock):
    plt, rel = (stock.elf.get_section_by_name(s) for s in ('.plt', '.rel.plt'))
    if plt is None or rel is None:
        return {}
    dyn = stock.elf.get_section(rel['sh_link'])
    return {int(plt['sh_addr']) + 20 + i * 12:
            dyn.get_symbol(r['r_info_sym']).name
            for i, r in enumerate(rel.iter_relocations())}


def function_ranges(stock):
    by_address = {}
    for name, sym in stock.symbols.items():
        if sym['st_info']['type'] == 'STT_FUNC' and sym['st_size'] and sym['st_shndx'] != 'SHN_UNDEF':
            address = int(sym['st_value']) & ~1
            by_address.setdefault(address, (name, int(sym['st_size'])))
    return by_address


def calls(stock, address, size, names):
    result = []
    for at in range(address, address + size - 3, 4):
        branch = direct_branch(at, stock.word(at))
        if not branch:
            continue
        target, kind, cond = branch
        if kind == 'b' and address <= target < address + size:
            continue
        result.append({'call': hex(at), 'target': hex(target), 'kind': kind,
                       'condition': cond, 'symbol': names.get(target)})
    return result


def assembly(stock, address, size, names):
    result = []
    # Literal pools in these ARM functions are deliberately retained as bytes;
    # an instruction-looking pool entry is not treated as a proven call.
    for ins in stock.decoder.disasm(stock.read(address, size), address):
        branch = direct_branch(ins.address, int.from_bytes(ins.bytes, 'little'))
        note = names.get(branch[0], '') if branch else ''
        result.append(f'{ins.address:08x}  {ins.bytes.hex()}  '
                      f'{ins.mnemonic:9s} {ins.op_str}' + (f' ; {note}' if note else ''))
    return '\n'.join(result)


def udd_analysis(stock, out):
    names = plt_names(stock)
    names.update({a: name for a, (name, _) in function_ranges(stock).items()})
    names.update({0x4bb5c: 'ipcs_debug_noop', 0x4f134: 'ipcs_reg_debug_noop'})
    functions, asm = {}, []
    for name in FUNCTIONS:
        if name not in stock.symbols:
            continue
        sym = stock.symbols[name]
        address, size = int(sym['st_value']), int(sym['st_size'])
        functions[name] = {'address': hex(address), 'size': size,
                           'calls': calls(stock, address, size, names)}
        asm.append(f'\n## {name} {address:#x} size={size}\n' + assembly(stock, address, size, names))
    for name, address, size in [('ipcs_debug_noop', 0x4bb5c, 28),
                                ('ipcs_reg_debug_noop', 0x4f134, 28)]:
        asm.append(f'\n## {name} {address:#x}\n' + assembly(stock, address, size, names))
        functions[name] = {'address': hex(address), 'size': size,
                          'calls': calls(stock, address, size, names)}
    # PIC literal data flow, reviewed against d4_get_udd_open_count/open/close.
    got_base = (0xde2dc + 8 + stock.word(0xde304)) & 0xffffffff
    counts = (got_base + stock.word(0xde308)) & 0xffffffff
    mutexes = (got_base + stock.word(0xde0c4)) & 0xffffffff
    thread_ids = (got_base + stock.word(0xde0cc)) & 0xffffffff
    outer_mutex = (0x4bb88 + 8 + stock.word(0x4bd90) + stock.word(0x4bd94)) & 0xffffffff
    fd = (0x4bb88 + 8 + stock.word(0x4bd90) + stock.word(0x4bd9c)) & 0xffffffff
    device_path = (got_base + stock.word(0x4bd98)) & 0xffffffff
    expected_device = b'/dev/d4_ipcs\0'
    if stock.read(device_path,len(expected_device)) != expected_device:
        raise ValueError('Unexpected IPCS device path')
    probe = {'udd_count_array': hex(counts), 'ipcs_count': hex(counts + 6 * 4),
             'udd_mutex_array': hex(mutexes), 'ipcs_udd_mutex': hex(mutexes + 6 * 24),
             'udd_thread_ids_array': hex(thread_ids), 'ipcs_mutex': hex(outer_mutex),
             'ipcs_fd': hex(fd), 'device_path_address': hex(device_path), 'index': 6, 'max_users': 50,
             'snapshot_is_not_atomic_with_open_close': True}
    (out/'udd.asm').write_text('\n'.join(asm), encoding='utf-8')
    (out/'udd-calls.json').write_text(json.dumps(functions, indent=2), encoding='utf-8')
    # A small fingerprint is checked in the target process by the observer.
    header = ['/* Generated from the verified NX300M 1.15 UDD ELF. */',
              '#ifndef NX_IPCS_OBSERVE_STOCK_H', '#define NX_IPCS_OBSERVE_STOCK_H',
              f'#define NX_UDD_COUNT {counts + 24:#x}U',
              f'#define NX_UDD_MUTEX {mutexes + 144:#x}U',
              f'#define NX_IPCS_MUTEX {outer_mutex:#x}U',
              f'#define NX_IPCS_FD {fd:#x}U',
              f'#define NX_IPCS_PATH {device_path:#x}U']
    for label, address in [('OPEN', 0x4bb78), ('COUNT', 0xde2cc), ('CLOSE', 0x4bdb8)]:
        data = stock.read(address, 32)
        header += [f'#define NX_VERIFY_{label}_ADDR {address:#x}U',
                   f'static const unsigned char nx_verify_{label.lower()}[32]={{' +
                   ','.join(f'0x{v:02x}' for v in data) + '};']
    header += ['#endif', '']
    (out/'ipcs_observe_stock.h').write_text('\n'.join(header), encoding='utf-8')
    return {'functions': functions, 'read_only_probe_addresses': probe,
            'open_count_zero': ['ipcs mutex', 'UDD mutex', 'libc open',
                                'reg info ioctl + mmap', 'IRQ open ioctl', 'ipcs_init', 'unlock'],
            'open_count_nonzero': ['ipcs mutex', 'UDD mutex', 'increment count + thread record', 'unlock'],
            'init': ['ipcs_reset', 'ipcs_sw_dma_reset', 'ipcs_default_regset_load', 'ipcs_cl_smdma_set'],
            'success_log_function': '0x4bb5c: no-op, no printf on success'}


class RawKernel:
    def __init__(self, path):
        self.path, self.raw = path, path.read_bytes()
        self.decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        self.decoder.skipdata = True

    def word(self, address):
        return struct.unpack_from('<I', self.raw, address - KBASE)[0]

    def read(self, address, size):
        return self.raw[address-KBASE:address-KBASE+size]

    def cstring(self, address):
        return self.read(address, 120).split(b'\0')[0].decode('ascii', errors='replace')

    def exports(self):
        if hasattr(self, 'exports_cache'):
            return dict(self.exports_cache)
        result = {}
        for off in range(0, len(self.raw)-8, 4):
            value, string = struct.unpack_from('<II', self.raw, off)
            at = string-KBASE
            if KBASE <= value < KBASE+0x400000 and 0x500000 < at < len(self.raw):
                name = self.raw[at:at+100].split(b'\0')[0]
                if re.fullmatch(rb'[A-Za-z_][A-Za-z_0-9]{1,95}', name):
                    result[value] = name.decode('ascii')
        self.exports_cache = result
        return dict(result)


def kernel_analysis(path, out):
    k = RawKernel(path)
    if hashlib.sha256(k.raw).hexdigest() != KERNEL_SHA:
        raise ValueError('Expected the verified 2014-12-08 kernel extracted from NX300M 1.15')
    names = k.exports()
    anchors = {0xc01712c4: '__const_udelay', 0xc0195728: 'd4_pmu_scpre_set',
               0xc01956dc: 'd4_pmu_scall_set', 0xc0019724: 'clk_enable',
               0xc0019680: 'clk_disable', 0xc0258b24: 'd4_rmu_request'}
    for address, name in anchors.items():
        if names.get(address) != name:
            raise ValueError(f'Kernel identity/anchor mismatch at {address:#x}: {name}')
    # miscdevice: minor=255, name=d4_ipcs, fops=0xc05375ac.
    if (k.word(0xc069166c) != 255 or k.cstring(k.word(0xc0691670)) != 'd4_ipcs'
            or k.word(0xc0691674) != 0xc05375ac):
        raise ValueError('Original kernel d4_ipcs miscdevice anchor mismatch')
    fops = {name: k.word(0xc05375ac+offset) for name, offset in
            [('read', 8), ('poll', 28), ('ioctl', 32), ('mmap', 40), ('open', 44), ('release', 52)]}
    if fops['open'] != KERNEL_FUNCTIONS['ipcs_k_open'][0] or fops['release'] != KERNEL_FUNCTIONS['ipcs_k_release'][0]:
        raise ValueError('IPCS fops binding mismatch')
    if k.cstring(0xc05beb70) != 'ipcs' or k.word(0xc01712b8) != 0x346dc:
        raise ValueError('Clock name or ARM udelay scale mismatch')
    names.update({a: name for name, (a, _) in KERNEL_FUNCTIONS.items()})
    functions, asm = {}, []
    for name, (a, z) in KERNEL_FUNCTIONS.items():
        functions[name] = {'address': hex(a), 'end_exclusive': hex(z),
                           'identity': 'export' if a in k.exports_cache else 'source-correlated; binary anchors retained',
                           'calls': calls(k, a, z-a, names),
                           'indirect_calls': [{'call': hex(i.address), 'instruction': i.mnemonic+' '+i.op_str}
                                              for i in k.decoder.disasm(k.read(a,z-a),a)
                                              if i.mnemonic.startswith('blx') and not i.op_str.startswith('#')]}
        asm.append(f'\n## {name} {a:#x}..{z:#x}\n' + assembly(k, a, z-a, names))
    power_calls = functions['ipcs_k_pmu_on_off']['calls']
    waits = [v for v in power_calls if v['symbol'] == 'ipcs_k_dma_rmu_bus']
    if [v['call'] for v in waits] != ['0xc02a94d4', '0xc02a9504']:
        raise ValueError('Unexpected IPCS power-up DMA wait calls')
    delay_asm = assembly(k, 0xc02a935c, 108, names)
    for text in ['#0xf44', '#0xf3c', '#0xf54', '#0xf48', '#0x64', '#0xd6f8', '#0xa3']:
        if text not in delay_asm:
            raise ValueError(f'DMA reset fingerprint missing {text}')
    (out/'kernel.asm').write_text('\n'.join(asm), encoding='utf-8')
    (out/'kernel-exports.json').write_text(json.dumps({hex(a): n for a, n in k.exports_cache.items()}, indent=2), encoding='utf-8')
    # The separate opener device exposes a read-only, mutex-protected count
    # and owner-list snapshot; its open/release do not open the IPCS device.
    for address in (0xc02b0180,0xc02b0188):
        if k.read(address,8)!=struct.pack('<II',0xe3a00000,0xe12fff1e):
            raise ValueError('Unexpected opener open/release implementation')
    if k.word(0xc0537974)!=0xc02b01a0 or k.word(0xc0537980)!=0xc02b0180 or k.word(0xc0537988)!=0xc02b0188:
        raise ValueError('Unexpected opener file_operations')
    if (k.word(0xc02b02b8),k.word(0xc02b02bc))!=(0xe306220d,0xe3442198):
        raise ValueError('Unexpected opener GET_PID_LIST command')
    if direct_branch(0xc02b0300,k.word(0xc02b0300))[0]!=0xc02b0570:
        raise ValueError('Unexpected opener snapshot dispatch')
    opener_code=assembly(k,0xc02b0180,0x48c,names)
    (out/'opener-interface.asm').write_text(opener_code+'\n',encoding='utf-8')
    return {'image': identity(path), 'base': hex(KBASE),
            'version': re.search(rb'Linux version [^\0\n]+', k.raw).group().decode(),
            'device_anchor': {'miscdevice': '0xc069166c', 'name': 'd4_ipcs',
                              'fops': '0xc05375ac', 'entries': {n: hex(a) for n, a in fops.items()}},
            'functions': functions,
            'opener_read_only': {'fops':'0xc0537954','ioctl':'0xc02b01a0',
                                'get_count_command':'0x4004620a','get_count':'0xc02b0544',
                                'get_pid_list_command':'0x4198620d','get_pid_list':'0xc02b0570',
                                'list_bytes':408,'owner_slots':100,'ipcs_index':6,
                                'snapshot_mutex_lock_call':'0xc02b05a4','snapshot_mutex_unlock_call':'0xc02b0600',
                                'open_release_are_noops':True,'live_interface_tested':False},
            'fixed_wait': {'function': 'ipcs_k_dma_rmu_bus', 'address': '0xc02a935c',
                           'power_up_calls': waits, 'each_nominal_us': 5000, 'power_up_nominal_us': 10000,
                           'method': '100 calls to __const_udelay(0xa3d6f8); 0xa3d6f8 == 50 * binary __udelay scale 0x346dc',
                           'type': 'CPU busy wait; preemption/IRQ may add wall time',
                           'runtime_branch_observed': False},
            'pmu_waits': {'wait_for_stable': {'address': '0xc019592c', 'nominal_timeout_us': 100000},
                          'pmu_wait_for_ack': {'address': '0xc01959f0', 'nominal_timeout_us': 400000},
                          'shared_mutex': hex(k.word(0xc01959ec))},
            'kernel_counts': {'array': '0xc06c4828', 'ipcs_count': '0xc06c4840',
                              'ipcs_mutex': '0xc06c48a0', 'ipcs_index': 6, 'max_users': 100,
                              'read_live': False,
                              'count_mutex_released_before_power_transition': True},
            'close_power_off': ['last kernel count -> PMU ack', 'isolation', 'ISP reset',
                                'clk_disable', 'clk_put', 'SCPRE/SCALL power gates'],
            'ioctl_calls': {'physical_reg_info': {'command': '0xc0087303', 'call': '0xc02aa004'},
                            'open_irq': {'command': '0x80047301', 'call': '0xc02aa040'},
                            'close_irq': {'command': '0x7302', 'call': '0xc02a9eb0'},
                            'pmu': {'command': '0x40047304', 'call': '0xc02a9c6c'},
                            'clock': {'command': '0x40047305', 'call': '0xc02aa1e4'}},
            'irq_state_scope': 'Kernel-global callback array is reset by interrupt_init; request_threaded_irq -EBUSY is accepted. Kernel count protects power lifetime, not callback ownership.',
            'limitations': ['Static branch identity does not establish per-frame execution.',
                            'Public source is older than this kernel; source-only claims must remain separate.',
                            'No live kernel identity, kernel count, CPU timings or LCD presentations observed.']}


def callers_analysis(rootfs, out):
    targets = {'ipcs_open', 'ipcs_close', 'ipcs_init', 'ipcs_pmu_on_off',
               'ipcs_set_clock', 'ipcs_set_clock_rate'}
    # The prior rootfs-wide scan identifies these direct importers. Refresh the
    # bytes and all ARM executable sections, including calls in stripped code.
    filenames = ['libcapture-fw-slpcam-nx300.so', 'libdi-alg-ipplatform.so',
                 'libfdfr.so', 'libiq-fw-slpcam.so', 'libmm-photo.so',
                 'libmmfhal.so.0.0.0', 'libsif.so']
    report, asm = [], []
    for filename in filenames:
        s = Stock(rootfs/'usr/lib'/filename)
        plts, ranges = plt_names(s), function_ranges(s)
        starts = sorted(ranges)
        hits = []
        for sec in s.elf.iter_sections():
            if not sec['sh_flags'] & 4 or sec['sh_type'] == 'SHT_NOBITS' or sec.name == '.plt':
                continue
            raw, base = sec.data(), int(sec['sh_addr'])
            for off in range(0, len(raw)-3, 4):
                at = base+off
                branch = direct_branch(at, struct.unpack_from('<I', raw, off)[0])
                if not branch or plts.get(branch[0]) not in targets:
                    continue
                pos = bisect.bisect_right(starts, at)-1
                a = starts[pos] if pos >= 0 else 0
                fn = ranges.get(a)
                owner = fn[0] if fn and at < a+fn[1] else None
                hits.append({'call': hex(at), 'symbol': plts[branch[0]],
                             'branch': branch[1], 'condition': branch[2],
                             'caller': owner, 'caller_address': hex(a) if owner else None})
        expanded = set()
        names = dict(plts)
        names.update({a: name for a, (name, _) in ranges.items()})
        inspect_names = [hit['caller'] for hit in hits if hit['caller']]
        if filename == 'libcapture-fw-slpcam-nx300.so':
            inspect_names += [
                '_ZN18CDDWrapperIPCS_B2YC1Ev',
                '_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreEv',
                '_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreE18eIPCS_SEMAPHORE_ID',
                '_ZN18CDDWrapperIPCS_B2Y16PrepareOperationEPFvvE',
                '_ZN18CDDWrapperIPCS_B2Y15FinishOperationEv',
            ]
        for name in inspect_names:
            if name not in expanded:
                expanded.add(name)
                sym = s.symbols[name]
                a, z = int(sym['st_value']), int(sym['st_size'])
                # Large playback/effect functions remain in the existing full
                # pipeline dump. Keep focused resource wrappers here.
                if z <= 2048:
                    asm.append(f'\n## {filename}:{name} {a:#x}\n' + assembly(s, a, z, names))
        report.append({'library': identity(s.path), 'calls': hits})
    (out/'client-calls.asm').write_text('\n'.join(asm), encoding='utf-8')
    (out/'client-calls.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return {'libraries': report,
            'scope': 'Refreshed ARM executable sections of seven known direct importers, including conditional BL and PLT tail calls. Dynamic/indirect resolution and unrelated ELF files are not exhaustive.',
            'b2y_global_arbitration_proven': False}


def timing_analysis(path):
    from analyze_smartfilter_timing import parse_log, analyze
    parsed = parse_log(path)
    checked = analyze(parsed)
    if not checked['ok']:
        raise ValueError(f'Invalid camera timing dump: {checked["errors"]}')
    excluded = {(v['dump_id'], v['seq']) for v in checked['excluded_records']}
    rows = []
    for dump_id, dump in parsed.dumps.items():
        for seq, (r, _) in dump.payload.items():
            if (r['type'] != 'sf_diag_record' or r['kind'] != 'frame' or r['path'] != 'render'
                    or r['state'] != 'committed' or r['flags'] & (4|8|256)
                    or (dump_id, seq) in excluded):
                continue
            times = {key: v['sec']*10**9+v['nsec'] for key, v in r['stages'].items() if v['calls']}
            rows.append({'dump_id': dump_id, 'seq': seq, 'tid': r['tid'],
                         'preview_ns': times['preview'], 'open_ns': times['open'],
                         'close_ns': times['close'], 'stages_ns': times})
    def stats(values):
        values = sorted(values)
        if not values:
            return {'n': 0}
        return {'n': len(values), **{f'p{q}_ns': values[math.ceil(len(values)*q/100)-1]
                                    for q in (50,95,99)}, 'max_ns': values[-1]}
    return {'source': identity(path), 'validated_by': 'analyze_smartfilter_timing.parse_log/analyze',
            'render_frames': len(rows),
            'distributions': {name: stats([v[name+'_ns'] for v in rows]) for name in ('preview','open','close')},
            'median_per_frame_open_fraction': statistics.median(v['open_ns']/v['preview_ns'] for v in rows),
            'longest_open_frames': sorted(rows, key=lambda v:v['open_ns'], reverse=True)[:5],
            'dropped': [d.begin['dropped'] for d in parsed.dumps.values()],
            'cpu_timing_present': False, 'open_count_present': False, 'lcd_presentations_present': False,
            'optimized_camera_run_present': False,
            'note': 'Retained steady render records only. No Open substage intervals or per-thread CPU clock; no inference of actual LCD FPS or live first-open frequency.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('--kernel', type=Path,
                        default=ROOT/'outputs/quickshare/stock-1.15-kernel-50ac20.bin')
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    rootfs = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    s = Stock(rootfs/'usr/lib/libudd.so.3.1')
    record = identity(s.path)
    if record['sha256'] != UDD_SHA or int(s.symbols['ipcs_open']['st_value']) != 0x4bb78:
        raise ValueError('Expected verified NX300M 1.15 UDD ELF')
    report = {'scope': 'static; no camera execution or writes', 'udd_image': record}
    report['udd'] = udd_analysis(s, out)
    report['kernel'] = kernel_analysis(args.kernel, out)
    report['clients'] = callers_analysis(rootfs, out)
    timing = ROOT/'outputs/deployment/smartfilter-preview/camera-sampled-20261102/latest-timing.ndjson'
    if timing.exists():
        report['timing'] = timing_analysis(timing)
        (out/'timing-evidence.json').write_text(json.dumps(report['timing'],indent=2),encoding='utf-8')
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(out/'report.json'), 'fixed_power_up_wait_us': 10000,
                      'live_probe_executed': False,
                      'client_call_sites': sum(len(v['calls']) for v in report['clients']['libraries'])}, indent=2))


if __name__ == '__main__':
    main()
