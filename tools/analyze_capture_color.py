"""Summarize captured IPCS setter arguments without assigning a color space."""
import argparse
import json
from pathlib import Path


def analyze(directory):
    records = []
    for path in sorted(Path(directory).rglob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(record, dict) or record.get('kind') not in ('gamma', 'ccm', 'rgb2ycc'):
            continue
        if record.get('capture_boundary') != 'input before RTLD_NEXT; not necessarily final hardware input':
            continue
        record = dict(record, file=str(path))
        records.append(record)
    records.sort(key=lambda r: (r['guest_pid'], r['sequence']))
    result, previous = [], {}
    for record in records:
        kind = record['kind']
        values = record['values_uint16' if kind == 'gamma' else 'values_uint32']
        count, limit = (8192, 65535) if kind == 'gamma' else (9, 0xffffffff)
        if len(values) != count or any(type(v) is not int or not 0 <= v <= limit for v in values):
            raise ValueError('Invalid observer values: '+record['file'])
        row = {key: record.get(key) for key in
               ('file', 'guest_pid', 'sequence', 'kind', 'caller_library',
                'next_provider', 'next_provider_returned', 'return_code')}
        if kind == 'gamma':
            row.update(words=count, minimum=min(values), maximum=max(values),
                       descending_adjacent_pairs=sum(b < a for a, b in zip(values, values[1:])),
                       first=values[0], last=values[-1])
        else:
            row['coefficients_raw_uint32'] = values
            row['coefficients_int32_bit_view'] = [v if v < 0x80000000 else v-0x100000000 for v in values]
        # Different processes can share sequence numbers and must not be compared
        # as if they were successive updates from a single camera process.
        group = (record['guest_pid'], str(Path(record['file']).parent), kind)
        if group in previous:
            earlier, old_values = previous[group]
            row['previous_sequence'] = earlier
            row['changed_words'] = sum(a != b for a,b in zip(old_values,values))
        previous[group] = (record['sequence'], values)
        result.append(row)
    return {'records': result, 'count': len(result),
            'scope': 'Observed setter-boundary arguments; no color-space or pixel-output inference',
            'warning': 'Eight slots per setter retain recent calls only; missing records do not prove a stage was unused.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = analyze(args.directory)
    text = json.dumps(report, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')


if __name__ == '__main__':
    main()
