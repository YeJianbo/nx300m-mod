"""Read a complete, format-checked snapshot of one compiled look."""
import hashlib
import struct


def read_tables(directory, metadata):
    names = ['table.d4lut']
    if metadata.get('native_stock_3d_table') == 'table-stock3d.d4lut':
        names.append('table-stock3d.d4lut')
    elif (directory/'table-stock3d.d4lut').exists():
        raise ValueError(f'Undeclared composed LUT: {directory}; rebuild this look')
    compiler = metadata.get('compiler', {})
    expected = compiler.get('tables', {})
    if metadata.get('input_space') != 'nx300m-ipcs' and set(expected) != set(names):
        raise ValueError(f'Incomplete compiled LUT metadata: {directory}')
    result = {}
    for name in names:
        path = directory/name
        data = path.read_bytes()
        if len(data) != 4380:
            raise ValueError(f'Invalid LUT table length: {path}')
        words = struct.unpack('<2190H', data)
        if any(v > 1023 or (i % 730 == 729 and v) for i, v in enumerate(words)):
            raise ValueError(f'Invalid 10-bit LUT table or padding: {path}')
        if name in expected and hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError(f'LUT changed during snapshot: {path}; retry after compilation finishes')
        result[name] = data
    return result
