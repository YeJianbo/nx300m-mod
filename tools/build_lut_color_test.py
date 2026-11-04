"""Package Hard Mono and the everyday style catalogue for the test card."""
import argparse
import json
from pathlib import Path
import shutil
import zipfile

from build_sd_bundle import bundle_looks
from cube_to_d4 import DEFAULT_METHOD, METHODS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/looks/lightroom-import/on-camera-test'


def build(*, method=DEFAULT_METHOD):
    root = OUT/'sd/nx300m-mod'
    if root.exists():
        expected = ROOT.resolve()/'outputs/looks/lightroom-import/on-camera-test/sd/nx300m-mod'
        if root.is_symlink() or root.resolve() != expected:
            raise ValueError('Unexpected catalogue staging path: '+str(root))
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    ids = bundle_looks(root, method=method, complete=True)
    shutil.copyfile(ROOT/'docs/LUT_ON_CAMERA_TEST.md', root/'LUT-TEST.md')
    if 'nx300-hard-mono' not in ids or any(key.startswith(('native-', 'test-fit-')) for key in ids):
        raise ValueError('Expected Hard Mono plus the formal style library')
    archive = OUT/'nx300m-lut-color-test.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                zipped.write(path, 'nx300m-mod/'+path.relative_to(root).as_posix())
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip():
            raise ValueError('Catalogue archive integrity failure')
    report = dict(archive=archive.relative_to(ROOT).as_posix(), looks=len(ids),
                  diagnostics=['nx300-hard-mono'], ab_pairs=[],
                  compiler_method=method or 'published', source_masters_modified=False, camera_written=False)
    (OUT/'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--method', choices=METHODS, default=DEFAULT_METHOD)
    args = parser.parse_args()
    print(json.dumps(build(method=args.method), ensure_ascii=False, indent=2))
