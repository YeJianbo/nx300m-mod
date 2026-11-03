"""Resolve the stock IPCS session coordination sites, without executing firmware."""
import json
import os
from pathlib import Path
import sys
import bisect

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from inspect_gamma_hook import Stock
from inspect_ipcs_lifecycle import ROOT, OUT, plt_names, direct_branch, identity, function_ranges

NAMES = (
    '_ZN18CDDWrapperIPCS_B2Y4OpenEv',
    '_ZN18CDDWrapperIPCS_B2Y5CloseEv',
    '_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreEv',
    '_ZN18CDDWrapperIPCS_B2Y16PrepareOperationEPFvvE',
    '_ZN18CDDWrapperIPCS_B2Y15FinishOperationEv',
    '_ZN9CLiveview11stopProcessENS_15eLIVEVIEW_STATEEb',
    '_ZN9CLiveview5StartEij',
)


def main():
    rf = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    cap = Stock(rf/'usr/lib/libcapture-fw-slpcam-nx300.so')
    plt = plt_names(cap)
    rows, asm = [], []
    for name in NAMES:
        sym = cap.symbols[name]
        address, size = int(sym['st_value']), int(sym['st_size'])
        slots = [a for a, r in cap.relocs.items() if r['symbol'] == name and r['type'] == 22]
        row = dict(symbol=name, address=hex(address), bytes=size, plt_slots=[hex(v) for v in slots], calls=[])
        asm.append(f'\n## {name} {address:#x} size={size}')
        for ins in cap.decoder.disasm(cap.read(address, size), address):
            branch = direct_branch(ins.address, int.from_bytes(ins.bytes, 'little'))
            callee = plt.get(branch[0]) if branch else None
            asm.append(f'{ins.address:08x} {ins.bytes.hex()} {ins.mnemonic:10} {ins.op_str}' +
                       (f' ; {callee}' if callee else ''))
            if callee:
                row['calls'].append(dict(address=hex(ins.address), symbol=callee))
        rows.append(row)
    kernel = (ROOT/'outputs/quickshare/stock-1.15-kernel-50ac20.bin').read_bytes()
    targets={a:n for a,n in plt.items() if n in NAMES}
    ranges=function_ranges(cap); starts=sorted(ranges); callers=[]
    for section in cap.elf.iter_sections():
        if not section['sh_flags']&4: continue
        raw=section.data(); base=int(section['sh_addr'])
        for i in range(0,len(raw)-3,4):
            branch=direct_branch(base+i,int.from_bytes(raw[i:i+4],'little'))
            if not branch or branch[0] not in targets: continue
            at=bisect.bisect_right(starts,base+i)-1
            name,size=ranges[starts[at]]
            callers.append(dict(call=hex(base+i),target=targets[branch[0]],
                                caller=name if base+i<starts[at]+size else 'anonymous',
                                caller_address=hex(starts[at])))
    report = dict(stock=identity(cap.path), sites=rows,
                  direct_callers=callers,
                  ikconfig_embedded=kernel.find(b'IKCFG_ST') >= 0,
                  kernel_trace_support='not established; inspect running tracefs and /proc/config.gz',
                  stock_liveview_persistent_ipcs_reference='not established',
                  lock_order='B2Y Open: lifecycle +0x44 before operation channel semaphores; Close releases it last',
                  candidate_order='admission gate -> drain frames -> owner lifecycle +0x44 -> channels 0..3 -> Open/Close -> release all',
                  prepare_is_too_late_for_lifecycle_wait=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'session-sites.asm').write_text('\n'.join(asm)+'\n', encoding='utf-8')
    (OUT/'session-sites.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
