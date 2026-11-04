"""Import the previously installed personal Lightroom LUT collection locally.

The installed RGB tables include the earlier tone repairs. They are the source
of truth rather than older downloaded cubes. Existing camera looks are retained.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
import zipfile

import numpy as np

from adobe_profiles import DcpProfile, xmp_metadata
from cube_to_d4 import compile_cube, load_cube
from import_lightroom_rgb_tables import CreativeRgbTable, decode_payload
from look_library import LookLibrary
from look_catalog import camera_looks
from lut_resample import sample_cube, sample_table, unpack
from lut_table_data import read_tables
from stock_lut import factory_3d_lut

ROOT = Path(__file__).resolve().parents[1]
CAMERA_RAW = Path.home()/'AppData/Roaming/Adobe/CameraRaw'
PROFILES = CAMERA_RAW/'Settings/通用相机与胶片风格'
OUT = ROOT/'outputs/looks/personal-import'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(library):
    return {m['id']: {p.name: digest(p.read_bytes())
                      for p in library.directory(m['id']).iterdir() if p.is_file()}
            for m in library.list()}


def save_report(output, report):
    pending = output/'inventory.json.tmp'
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    pending.replace(output/'inventory.json')


def display_name(name):
    label = re.sub(r'\s*\[(?:ColorReps模拟|模拟|原创)\]', '', name)
    label = label.replace(' · ', ' ').replace('风雅 Miyabi', '风雅')
    if '[ColorReps模拟]' in name:
        label = 'CR '+label
    return label


def creative_identity(profile):
    return digest(json.dumps([profile.get(tag) for tag in (50981, 50982, 51108)],
                             separators=(',', ':')).encode())


def write_readme(output, report):
    lines = ['# 之前的 Lightroom 风格导入', '',
             f'导入 {len(report["entries"])} 款；当前机内库 {report["total_looks"]} 款。', '',
             'CR 前缀表示原来的 ColorReps 风格，和已有同名风格分开。', '',
             '主包仅更新 LUT 表和目录；legacy 包只回退本批新增风格的编译表。',
             '解压到卡根目录，得到 nx300m-mod/looks/ 和 nx300m-mod/looks.txt。',
             '保留当前运行时、启动脚本和选择状态。', '',
             '| 名称 | 来源 |', '| --- | --- |']
    lines.extend(f'| {e["label"]} | {e["resource"]} |' for e in report['entries'])
    lines.extend(['', '未加入项：'])
    lines.extend(f'- {e["resource"]}：{e["reason"]}' for e in report['skipped'])
    (output/'README.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def run(profiles=PROFILES, output=OUT, *, include_dcp=True):
    profiles, output = Path(profiles), Path(output)
    paths = sorted(profiles.glob('*.xmp'))
    if not paths:
        raise ValueError('No personal LUT profiles found: '+str(profiles))
    output.mkdir(parents=True, exist_ok=True)
    library = LookLibrary()
    before = snapshot(library)
    previous = {}
    if (output/'inventory.json').exists():
        saved = json.loads((output/'inventory.json').read_text(encoding='utf-8'))
        before = saved['preexisting_files']
        previous = {e['id']: e for e in saved['entries']}
    report = {'schema': 1, 'source_session': '01a11697-091c-7be2-ba47-f3105d8607d2',
              'compiler': 'fit', 'preexisting_files': before, 'entries': [], 'skipped': [],
              'source_profiles': [], 'preexisting_preserved': False, 'complete': False,
              'model': 'sRGB code / trilinear D4 model; native ISP calibration unmeasured'}
    rng = np.random.default_rng(11032026)
    gray = np.repeat(np.linspace(0, 1, 257)[:, None], 3, axis=1)
    hold = np.concatenate((rng.integers(0, 256, (16000, 3))/255, gray))
    check = np.concatenate((rng.random((641, 3)), gray))
    legacy_archive = output/'nx300m-personal-luts-legacy.zip'
    with zipfile.ZipFile(legacy_archive, 'w', compression=zipfile.ZIP_DEFLATED) as legacy:
        def finish(key, resource, source_hash, table=None, cube=None):
            metadata = library.get(key)
            directory = library.directory(key)
            native = read_tables(directory, metadata)
            if cube is None:
                if table is not None:
                    # This file was generated above with exactly four header
                    # lines; avoid a second general-purpose shlex parse.
                    values = np.loadtxt(directory/'source.cube', skiprows=4)
                    if values.shape != (65**3, 3):
                        raise ValueError('Incomplete generated master: '+key)
                    cube = 65, (0.,)*3, (1.,)*3, values
                else:
                    cube = load_cube(directory/'source.cube')
            original = sample_cube(cube, hold)
            baseline = compile_cube(cube, method='legacy')
            error = lambda a, b: float(np.sqrt(np.mean((a-b)**2))*255)
            stats = {'legacy_rgb8_rmse': error(sample_table(baseline, hold), original),
                     'fit_rgb8_rmse': error(sample_table(native['table.d4lut'], hold), original),
                     'gray_fit_rgb8_rmse': error(sample_table(native['table.d4lut'], gray),
                                                 sample_cube(cube, gray))}
            if table is not None:
                stats['source_bake_rgb8_rmse'] = error(sample_cube(cube, hold), table.apply(hold))
            old_tables = {'table.d4lut': baseline,
                          'table-stock3d.d4lut': compile_cube(cube, factory_3d_lut(), method='legacy')}
            fallback = copy.deepcopy(metadata)
            fallback['compiler'] = dict(library._compiler_inputs(directory, metadata, 'legacy'),
                                        model=metadata['compiler']['model'],
                                        tables={n: digest(b) for n, b in old_tables.items()})
            for n, b in old_tables.items():
                legacy.writestr(f'nx300m-mod/looks/{key}/{n}', b)
            legacy.writestr(f'nx300m-mod/looks/{key}/look.json',
                            json.dumps(fallback, ensure_ascii=False, indent=2)+'\n')
            entry = {'id': key, 'label': metadata['label'], 'resource': resource,
                     'source_sha256': source_hash, 'metrics': stats}
            report['entries'].append(entry)
            save_report(output, report)
            print(json.dumps(entry, ensure_ascii=False), flush=True)

        with tempfile.TemporaryDirectory(prefix='personal-lut-', dir=output) as temporary:
            for path in paths:
                raw_xmp = path.read_bytes()
                m = xmp_metadata(path)
                resource = path.name
                attributes = m['settings']
                report['source_profiles'].append({'resource': resource, 'sha256': digest(raw_xmp)})
                if m['profile_name'].startswith('理光 '):
                    report['skipped'].append({'resource': resource, 'reason': '理光保留指定的 GR Neg／GR Pos 两项'})
                    continue
                if m['base_profile']:
                    raise ValueError('Profile requires a camera base: '+resource)
                fingerprint = attributes['RGBTable']
                table = CreativeRgbTable(decode_payload(attributes['Table_'+fingerprint], fingerprint))
                if (table.primaries, table.gamma, table.gamut) != (0, 1, 0):
                    raise ValueError('Personal profile has a different colour model: '+resource)
                # Independent standard-cube addressing verifies RGB axis order
                # and interpolation against the decoded Adobe table.
                nodes = table.values.reshape(table.size, table.size, table.size, 3)
                decoded_cube = (table.size, (0.,)*3, (1.,)*3,
                                nodes.transpose(2, 1, 0, 3).reshape(-1, 3))
                parity = float(np.max(np.abs(table.apply(check)-sample_cube(
                    decoded_cube, check, interpolation='tetrahedral'))))
                if parity > 1e-12:
                    raise AssertionError('RGBTable addressing differs: '+resource)
                key = 'lr-custom-'+fingerprint.lower()
                if library.directory(key).exists():
                    existing = library.get(key)
                    if existing.get('source_rgb_table_id') != fingerprint:
                        raise FileExistsError('Existing look differs: '+key)
                    if key in previous and previous[key]['source_sha256'] != digest(raw_xmp):
                        raise ValueError('Source changed during import: '+resource)
                    cube = None
                else:
                    # Retain the native 32^3 samples and evaluate their
                    # tetrahedral source at a denser 65^3 portable master.
                    master = Path(temporary)/(key+'.cube')
                    table.write_cube(master, 65)
                    metadata = library.add_cube(master, key, label=display_name(m['profile_name']),
                                                input_space='srgb', method='fit')
                    metadata.pop('source_path', None)
                    metadata.update(source_kind='adobe-rgb-table', source_resource=resource,
                                    source_collection='personal-lightroom', source_rgb_table_id=fingerprint,
                                    source_profile_sha256=digest(raw_xmp), source_grid=table.size,
                                    source_primaries='sRGB', source_gamma='sRGB', source_gamut='clip',
                                    source_interpolation='tetrahedral', source_baked_amount=1.,
                                    source_monochrome=table.monochrome,
                                    scope='creative RGBTable; installed tone repairs retained',
                                    source_decoder_max_abs_error=parity)
                    library._save(key, metadata)
                    master.unlink()
                    cube = None
                finish(key, resource, digest(raw_xmp), table, cube)

        if include_dcp:
            known = {}
            for m in library.list():
                path = Path(m.get('source_path', ''))
                if m.get('source_kind') == 'dcp-creative-look' and path.is_file():
                    known[creative_identity(DcpProfile(path))] = m['id']
            source_dir = CAMERA_RAW/'CameraProfiles/Imported/NX300_Pentax_Direct_Tests'
            for name in ('Bright', 'Natural', 'Portrait', 'Vibrant'):
                path = source_dir/f'NX P {name}.dcp'
                profile = DcpProfile(path)
                identity = creative_identity(profile)
                key = 'nx300-pentax-'+name.lower()
                resource = 'NX300_Pentax_Direct_Tests/'+path.name
                if identity in known and known[identity] != key:
                    report['skipped'].append({'resource': resource, 'reason': '色彩表已存在：'+known[identity]})
                    continue
                if not library.directory(key).exists():
                    metadata = library.add_dcp_look(path, key, label='宾得 '+name, method='fit')
                    metadata.pop('source_path', None)
                    metadata.update(source_resource=resource, source_collection='personal-lightroom',
                                    source_profile_sha256=digest(path.read_bytes()))
                    library._save(key, metadata)
                finish(key, resource, digest(path.read_bytes()))
                known[identity] = key
    report['preexisting_preserved'] = all(
        digest((library.directory(key)/name).read_bytes()) == expected
        for key, files in before.items() for name, expected in files.items())
    if not report['preexisting_preserved']:
        raise AssertionError('An existing look changed')
    report['total_looks'] = len(library.list())
    if report['total_looks'] > 256:
        raise ValueError('Native catalogue exceeds 256 entries')
    report['metrics'] = {name: float(np.mean([e['metrics'][name] for e in report['entries']]))
                         for name in ('legacy_rgb8_rmse', 'fit_rgb8_rmse', 'gray_fit_rgb8_rmse')}
    report['fit_rmse_regressions'] = [e['id'] for e in report['entries']
                                     if e['metrics']['fit_rgb8_rmse'] > e['metrics']['legacy_rgb8_rmse']+1e-9]
    # Publish only the source-checked library. Runtime binaries and selection
    # state are deliberately absent from this catalogue-only update.
    archive = output/'nx300m-look-library.zip'
    rows = []
    expected_files = {}
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for metadata in library.list():
            key = metadata['id']
            directory = library.directory(key)
            portable = {k: v for k, v in metadata.items() if k != 'source_path'}
            items = dict(read_tables(directory, metadata))
            items['look.json'] = (json.dumps(portable, ensure_ascii=False, indent=2)+'\n').encode()
            for name, data in items.items():
                target = f'nx300m-mod/looks/{key}/{name}'
                zipped.writestr(target, data)
                expected_files[target] = digest(data)
        for metadata in camera_looks(library):
            rows.append('|'.join([metadata['id'], library.label(metadata['label']), *['0']*7])+'\n')
        catalogue = ''.join(rows).encode('utf-8')
        zipped.writestr('nx300m-mod/looks.txt', catalogue)
        expected_files['nx300m-mod/looks.txt'] = digest(catalogue)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() or set(zipped.namelist()) != set(expected_files):
            raise AssertionError('Catalogue archive differs')
        for name, expected in expected_files.items():
            if digest(zipped.read(name)) != expected:
                raise AssertionError('Archive content differs: '+name)
    with zipfile.ZipFile(legacy_archive) as zipped:
        if zipped.testzip():
            raise AssertionError('Legacy archive CRC failed')
    report.update(complete=True, archive=archive.name, archive_files=len(expected_files),
                  legacy_archive=legacy_archive.name, archive_verified=True,
                  source_profiles_preserved=all(digest((profiles/e['resource']).read_bytes()) == e['sha256']
                                                for e in report['source_profiles']))
    if not report['source_profiles_preserved']:
        raise AssertionError('An installed Lightroom profile changed')
    save_report(output, report)
    write_readme(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=PROFILES)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--no-dcp', action='store_true')
    args = parser.parse_args()
    result = run(args.profiles, args.output, include_dcp=not args.no_dcp)
    print(json.dumps({k: result[k] for k in ('complete', 'total_looks', 'preexisting_preserved',
                                           'source_profiles_preserved', 'metrics', 'skipped')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
