"""Read the coherent software CS cache captured at the PP memory-pass boundary."""
import argparse
import json
from pathlib import Path
import struct


def node(payload, columns, rows, x, y, lane):
    """Stock InitCsParam storage: even spatial rows first, then odd rows."""
    if not columns or not rows or rows % 2 or len(payload) != columns*rows*8:
        raise ValueError('Invalid CS grid dimensions or payload size')
    if not (0 <= x < columns and 0 <= y < rows and 0 <= lane < 4):
        raise ValueError('CS node coordinate outside grid')
    stored_row = (rows//2 if y & 1 else 0) + y//2
    return struct.unpack_from('<H', payload, (stored_row*columns+x)*8+lane*2)[0]


def inspect(snapshot, task):
    data = snapshot.read_bytes()
    if len(data) < 20:
        raise ValueError('Truncated CS cache header')
    source, size, generation, valid, error = struct.unpack_from('<IIIIi', data)
    if valid not in (0, 1) or len(data) != 20+(size if valid else 0):
        raise ValueError('Incomplete CS cache payload')
    result = dict(source=f'0x{source:08x}', bytes=size, generation=generation,
                  valid=bool(valid), error=error, snapshot=str(snapshot),
                  scope='software cache at original PP memory-pass start',
                  hardware_color_lane_order_verified=False, pixels_processed=False)
    if not valid:
        return result
    if error or not size or not generation:
        raise ValueError('Inconsistent valid CS cache metadata')
    task_data = task.read_bytes()
    if len(task_data) != 61*4:
        raise ValueError('Unexpected PP task layout')
    words = struct.unpack('<61I', task_data)
    if words[0] != 1:
        raise ValueError('Unsupported PP task version')
    source_at_start, size_at_start, columns, rows = words[15:19]
    payload = data[20:]
    # A matching source alone is insufficient: this address is reused by stock.
    result.update(columns=columns, rows=rows,
                  source_matches_task=source==source_at_start,
                  size_matches_task=size==size_at_start,
                  corners={f'{x},{y}':[node(payload,columns,rows,x,y,lane) for lane in range(4)]
                           for x,y in ((0,0),(columns-1,0),(0,rows-1),(columns-1,rows-1))})
    values = struct.unpack('<'+'H'*(size//2), payload)
    result['lanes'] = [dict(minimum=min(values[i::4]), maximum=max(values[i::4]),
                            distinct_values=len(set(values[i::4]))) for i in range(4)]
    result['all_unity_8192'] = all(v==8192 for v in values)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    result = inspect(args.capture/'nx300m-pp-cs-at-memory.bin',
                     args.capture/'nx300m-pp-memory-task.bin')
    encoded = json.dumps(result, indent=2)+'\n'
    (args.capture/'cs-cache.json').write_text(encoded, encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    main()
