"""Build separate NX300M native-code LUT probes without changing the look library."""
import json
from pathlib import Path
import re
import struct

from cube_to_d4 import compile_cube
from stock_lut import factory_3d_lut

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/deployment/lut-diagnostics'
ROOT = OUTPUT / 'sd/nx300m-mod'
WORDS_PER_PLANE = 730
TABLE_BYTES = 4380
PROBES = (
    ('native-identity', '诊断01 原生恒等（颜色域未知）', None),
    ('native-equal-channels', '诊断02 三通道均值（RGB假设）', 'mean'),
    ('native-axis0-shift', '诊断03 原生轴0 +64（颜色域未知）', 0),
    ('native-axis1-shift', '诊断04 原生轴1 +64（颜色域未知）', 1),
    ('native-axis2-shift', '诊断05 原生轴2 +64（颜色域未知）', 2),
)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def validate_table(data):
    if len(data) != TABLE_BYTES:
        raise ValueError(f'Expected {TABLE_BYTES} table bytes, got {len(data)}')
    words = struct.unpack('<2190H', data)
    if max(words) > 1023:
        raise ValueError('Diagnostic table exceeds the 10-bit code range')
    if any(words[index] for index in (729, 1459, 2189)):
        raise ValueError('Diagnostic table has nonzero plane padding')
    return words


def transform(words, operation):
    """Apply F to integer native output codes, leaving each plane pad zero."""
    result = [0] * 2190
    for index in range(729):
        codes = [words[channel * WORDS_PER_PLANE + index] for channel in range(3)]
        if operation == 'mean':
            codes = [sum(codes) // 3] * 3
        elif operation is not None:
            codes[operation] = min(1023, codes[operation] + 64)
        for channel, value in enumerate(codes):
            result[channel * WORDS_PER_PLANE + index] = value
    return struct.pack('<2190H', *result)


def build():
    # Reuse only the compiler's grid quantisation and storage order. The names
    # in that compiler do not establish the camera's internal colour domain.
    identity_cube = (2, (0.,) * 3, (1.,) * 3,
                     [(axis0, axis1, axis2) for axis2 in (0., 1.)
                      for axis1 in (0., 1.) for axis0 in (0., 1.)])
    identity = validate_table(compile_cube(identity_cube))
    factory = tuple(factory_3d_lut())
    factory_bytes = struct.pack('<2190H', *factory)
    validate_table(factory_bytes)
    looks = ROOT / 'looks'
    looks.mkdir(parents=True, exist_ok=True)
    rows, inventory = [], []
    for key, label, operation in PROBES:
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,62}', key):
            raise ValueError(f'Invalid diagnostic ID: {key}')
        directory = looks / key
        directory.mkdir(exist_ok=True)
        formula = ('F(c) = c' if operation is None else
                   'F(c) = (m,m,m), m = floor((c0+c1+c2)/3)' if operation == 'mean' else
                   f'F(c)[{operation}] = min(1023, c{operation}+64); other codes unchanged')
        for name, source, source_name in (
            ('table.d4lut', identity, 'quantized native identity grid'),
            ('table-stock3d.d4lut', factory, 'factory still 3D LUT output codes'),
        ):
            path = directory / name
            path.write_bytes(transform(source, operation))
            words = validate_table(path.read_bytes())
            inventory.append({'path': path.relative_to(ROOT).as_posix(),
                              'bytes': path.stat().st_size, 'source': source_name,
                              'minimum': min(words), 'maximum': max(words),
                              'padding_zero': True})
        metadata = {
            'id': key, 'label': label, 'diagnostic': True,
            'source_kind': 'native-code-diagnostic',
            'input_space': 'nx300m-ipcs', 'output_space': 'nx300m-ipcs',
            'native_table': 'table.d4lut',
            'native_stock_3d_table': 'table-stock3d.d4lut',
            'native_format': {'grid': 9, 'channels': 3, 'bits': 10,
                              'bytes': TABLE_BYTES, 'words_per_plane': WORDS_PER_PLANE,
                              'axis_order': 'axis0 slowest, axis1 middle, axis2 fastest'},
            'calibration': {'camera': 'NX300M', 'status': 'unknown'},
            'transform': formula,
            'table_sources': {'table.d4lut': 'F(quantized native identity grid)',
                             'table-stock3d.d4lut': 'F(factory_3d_lut)'},
            'interpretation': ('三通道整数均值，仅用于观察输出是否无彩色；RGB假设未确认，'
                               '不据此认定黑白转换正确。' if operation == 'mean' else
                               '仅定义原生数据轴变换；实际颜色、亮度及硬件响应未知。'),
            'runtime_status': 'generated-not-loaded',
        }
        path = directory / 'look.json'
        write_json(path, metadata)
        inventory.append({'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size})
        rows.append({'id': key, 'label': label, 'transform': formula})
    catalog = ROOT / 'looks-diagnostics.txt'
    catalog.write_text(''.join('|'.join([row['id'], row['label'], *['0'] * 7]) + '\n'
                               for row in rows), encoding='utf-8', newline='\n')
    inventory.append({'path': catalog.relative_to(ROOT).as_posix(), 'bytes': catalog.stat().st_size})
    actual_ids = sorted(path.name for path in looks.iterdir() if path.is_dir())
    expected_ids = sorted(row['id'] for row in rows)
    if actual_ids != expected_ids or len(catalog.read_text(encoding='utf-8').splitlines()) != 5:
        raise ValueError('Diagnostic directory/catalog does not contain exactly five probes')
    composed_identity = (looks / 'native-identity/table-stock3d.d4lut').read_bytes()
    if composed_identity != factory_bytes:
        raise ValueError('Identity composition differs from the factory table')
    manifest = {'version': 1, 'root': ROOT.relative_to(PROJECT).as_posix(),
                'catalog': catalog.name, 'look_count': len(rows), 'looks': rows,
                'files': inventory}
    write_json(OUTPUT / 'manifest.json', manifest)
    report = {
        'passed': True, 'output': OUTPUT.relative_to(PROJECT).as_posix(),
        'look_count': len(rows), 'table_count': len(rows) * 2,
        'ids': expected_ids,
        'purpose': '单独生成原生数据轴探针，供后续受控机身观察；不用于色卡拟合或胶片风格评价。',
        'coordinate_contract': {'axis_order': 'axis0 slowest, axis2 fastest',
                                'index': '(axis0*9+axis1)*9+axis2',
                                'identity_codes': [int(1023 * i / 8) for i in range(9)],
                                'source_domain': 'unknown NX300M IPCS native codes'},
        'expected_data_changes': rows,
        'validation': {'exactly_five_probes': True, 'ten_tables_checked': True,
                       'bytes_per_table': TABLE_BYTES, 'maximum_code': 1023,
                       'all_plane_padding_zero': True,
                       'identity_composed_matches_factory_bytes': True},
        'image_verified': False, 'hardware_verified': False,
        'unresolved': ['实际输入和输出颜色域', '各数据轴对应的实际图像响应',
                       '三通道相等是否产生无彩色输出'],
        'scope': '本地数据生成与格式检查；未修改主 looks 库、未加载相机、未运行图像拟合。',
    }
    write_json(OUTPUT / 'build-report.json', report)
    return report


if __name__ == '__main__':
    result = build()
    print(f"LUT diagnostics built: {result['look_count']} probes, "
          f"{result['table_count']} tables; identity composition matches factory bytes")
