"""Check the formal 80-look package, legacy rollback and compiler cache."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import zipfile

from build_lut_color_test import build, OUT as CATALOGUE
from look_library import LookLibrary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/deployment/lut-fit'
KEYS = ('nx300-hard-mono','nx300-fuji-classic-negative','ricoh-positive-exact')


def main():
    lib = LookLibrary()
    with zipfile.ZipFile(OUT/'legacy-library.zip') as old:
        manifest = json.loads(old.read('manifest.json'))
        looks = lib.list()
        assert set(manifest)=={m['id'] for m in looks}
        for m in looks:
            key = m['id']; directory = lib.directory(key)
            assert m['compiler']['method']=='fit',key
            assert hashlib.sha256((directory/'source.cube').read_bytes()).hexdigest()==manifest[key]['source_sha256'],key
        # Check the real legacy implementation against the archived pre-change
        # tables, then switch back with the default API. Use private copies.
        with tempfile.TemporaryDirectory(prefix='library-',dir=OUT) as temp:
            trial = LookLibrary(Path(temp))
            for key in KEYS:
                directory = trial.directory(key); directory.mkdir()
                shutil.copyfile(lib.directory(key)/'source.cube',directory/'source.cube')
                (directory/'look.json').write_bytes(old.read(key+'/look.json'))
                legacy = trial.ensure_compiled(key,method='legacy')
                for name in manifest[key]['tables']:
                    assert (directory/name).read_bytes()==old.read(key+'/'+name),(key,name)
                assert legacy['compiler']['method']=='legacy'
                fit = trial.ensure_compiled(key)
                assert fit['compiler']['method']=='fit'
                for name in fit['compiler']['tables']:
                    assert (directory/name).read_bytes()==(lib.directory(key)/name).read_bytes(),(key,name)
                mtimes = {p.name:p.stat().st_mtime_ns for p in directory.iterdir()}
                trial.ensure_compiled(key)
                assert mtimes=={p.name:p.stat().st_mtime_ns for p in directory.iterdir()}
        package = build()
        archive = ROOT/package['archive']
        changed = 0; count = 0; packaged = CATALOGUE/'sd/nx300m-mod'
        with zipfile.ZipFile(archive) as z:
            for m in looks:
                key = m['id']
                meta = json.loads(z.read(f'nx300m-mod/looks/{key}/look.json'))
                assert meta['compiler']['method']=='fit'
                for name,digest in meta['compiler']['tables'].items():
                    data = z.read(f'nx300m-mod/looks/{key}/{name}')
                    assert data==(lib.directory(key)/name).read_bytes()
                    assert hashlib.sha256(data).hexdigest()==digest
                    assert len(data)==4380
                    words = struct.unpack('<2190H',data)
                    assert max(words)<=1023 and not any(words[i] for i in (729,1459,2189))
                    changed += data!=old.read(key+'/'+name); count += 1
        mono = struct.unpack('<2190H',(packaged/'looks/nx300-hard-mono/table.d4lut').read_bytes())
        assert mono[:730]==mono[730:1460]==mono[1460:]
        # A complete table-only fallback for the current library. No runtime
        # binary, hook or shortcut setting is changed by this archive.
        rollback = OUT/'nx300m-lut-legacy.zip'
        with zipfile.ZipFile(rollback,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for m in looks:
                key = m['id']; meta = json.loads(old.read(key+'/look.json'))
                meta.pop('source_path',None)
                meta['compiler'] = dict(method='legacy',source_sha256=manifest[key]['source_sha256'],
                                        tables=manifest[key]['tables'],origin='pre-fit library archive')
                z.writestr(f'nx300m-mod/looks/{key}/look.json',json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
                for name in manifest[key]['tables']:
                    z.writestr(f'nx300m-mod/looks/{key}/{name}',old.read(key+'/'+name))
            z.writestr('nx300m-mod/looks.txt',(packaged/'looks.txt').read_bytes())
            z.writestr('nx300m-mod/RESTART_SERVICE','lut-legacy-20261103\n')
        with zipfile.ZipFile(rollback) as z:
            assert z.testzip() is None
    report = dict(passed=True,method='fit',looks=len(looks),tables=count,changed_tables=changed,
                  source_masters_unchanged=True,hard_mono_planes_equal=True,
                  default_fit_and_explicit_legacy_checked=list(KEYS),
                  unchanged_inputs_skip_recompile=True,formal_package_byte_exact=True,
                  formal_archive=package['archive'],legacy_archive=rollback.relative_to(ROOT).as_posix(),
                  physical_color_verified=False)
    (OUT/'workflow-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
