"""Check published-catalog UI deployment without recompiling or launching ARM."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import metol_stock
from look_library import LookLibrary
from look_catalog import camera_looks
from types import SimpleNamespace


def main():
    library = LookLibrary()
    entries = camera_looks(library)
    calls = []

    def dispatch(command, **kwargs):
        assert command[-3] == 'python3' and command[-2].endswith('deploy_simulator_assets.py'), command
        payload = json.loads(kwargs['input'])
        assert 'looks.txt' in payload['files'] and 'lut_engine.arm' in payload['files']
        calls.append({'command': command, 'stdin_bytes': len(kwargs['input'])})
        return SimpleNamespace(stdout=b'{"copied":0,"unchanged":1,"removed":0}\n')

    with tempfile.TemporaryDirectory(dir=metol_stock.PROJECT/'outputs/metol', prefix='catalog-probe-') as tmp:
        with patch.object(metol_stock, 'LookLibrary', return_value=library), \
                patch.object(metol_stock.subprocess, 'run', side_effect=dispatch), \
                patch.object(library, 'ensure_compiled', side_effect=AssertionError('UI invoked compiler')):
            published = metol_stock._deploy_looks(1, Path(tmp), method=None)
            assert [m['id'] for m in published] == [m['id'] for m in entries]
            assert all((Path(tmp)/m['id']/'table.d4lut').is_file() for m in entries)
    # Explicit synchronization retains method selection; no fitter is run here.
    with tempfile.TemporaryDirectory(dir=metol_stock.PROJECT/'outputs/metol', prefix='catalog-probe-') as tmp:
        with patch.object(metol_stock, 'LookLibrary', return_value=library), \
                patch.object(metol_stock.subprocess, 'run', side_effect=dispatch), \
                patch.object(library, 'ensure_compiled', side_effect=lambda key, **kw: library.get(key)) as compile_call:
            metol_stock._deploy_looks(1, Path(tmp), method='legacy')
            assert compile_call.call_count == len(entries)
            assert all(c.kwargs == {'method': 'legacy'} for c in compile_call.call_args_list)
    report = {'passed': True, 'published_looks': len(entries), 'tables_format_and_hash_checked': True,
              'ui_invoked_compiler': False, 'explicit_legacy_routed': True,
              'dispatch': calls, 'actual_wsl_copy_executed': False}
    output = metol_stock.PROJECT/'outputs/review/runtime-locks/catalog-deploy.json'
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
