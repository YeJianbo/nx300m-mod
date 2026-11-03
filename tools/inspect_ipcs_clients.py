"""Disassemble the original direct IPCS clients and their arbitration calls."""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from inspect_gamma_hook import Stock
from inspect_ipcs_lifecycle import ROOT, OUT, identity, plt_names, direct_branch, function_ranges


def main():
    original = json.loads((OUT/'client-calls.json').read_text(encoding='utf-8'))
    report, assembly = [], []
    for record in original:
        stock = Stock(Path(record['library']['file']))
        names = {at: name for at, (name, _) in function_ranges(stock).items()}
        names.update(plt_names(stock))
        functions = []
        for name in dict.fromkeys(c['caller'] for c in record['calls']):
            sym = stock.symbols[name]
            address, size = int(sym['st_value']), int(sym['st_size'])
            assembly.append(f'\n## {stock.path.name} {name} {address:#x} bytes={size}')
            calls = []
            for ins in stock.decoder.disasm(stock.read(address, size), address):
                branch = direct_branch(ins.address, int.from_bytes(ins.bytes, 'little'))
                callee = names.get(branch[0]) if branch else None
                assembly.append(f'{ins.address:08x} {ins.bytes.hex()} {ins.mnemonic:10} {ins.op_str}' +
                                (f' ; {callee}' if callee else ''))
                if callee:
                    calls.append(dict(at=hex(ins.address), target=hex(branch[0]), name=callee))
            functions.append(dict(name=name, at=hex(address), bytes=size, calls=calls))
        report.append(dict(library=identity(stock.path), functions=functions))
    (OUT/'client-arbitration.asm').write_text('\n'.join(assembly)+'\n', encoding='utf-8')
    (OUT/'client-arbitration.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    for lib in report:
        print(Path(lib['library']['file']).name)
        for function in lib['functions']:
            print(' ', function['name'])
            for call in function['calls']:
                if any(word in call['name'].lower() for word in ('semaphore', 'mutex', 'ipcs_open', 'ipcs_close', 'stop', 'start')):
                    print('   ', call['at'], call['name'])


if __name__ == '__main__':
    main()
