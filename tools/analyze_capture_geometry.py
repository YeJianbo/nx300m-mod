"""Compare captured CCapImage geometry and every saved before/after RAM sample.

Reads copied trace files only. No camera process, pixel implementation or DMA
model is invoked. Equality is a byte observation, not proof that no write ran.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[1]
GEOMETRY = ('GWidth', 'GHeight', 'Width', 'Height', 'OffsetX', 'OffsetY')
DECIMAL = {'version', 'sequence', 'physical_dma_extent_proved', 'plane', 'slot',
           'row', 'stride', 'rows', 'bytes', 'rows_effective', 'rows_global',
           'row_bytes', 'offset_applied', *GEOMETRY,
           'ImageFormat', 'RawPackBit', 'RawStride', 'AddrType'}
HEX = {'object', 'physical', 'virtual', 'address', 'wrapper'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(text):
    result = {}
    for key, value in re.findall(r'(\w+)=([^\s]+)', text):
        result[key] = int(value, 10) if key in DECIMAL else (0 if value == '(nil)' else int(value, 16)) if key in HEX else value
    return result


def load_snapshot(path, required_roles=('input', 'output')):
    snapshot = {'metadata_file': str(path), 'header': {}, 'images': {}, 'bases': {},
                'layouts': {}, 'samples': {}}
    blobs = {}
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        item = fields(line)
        if line.startswith('version='):
            snapshot['header'] = item
        elif line.startswith('image '):
            snapshot['images'][item['role']] = item
        elif line.startswith('base '):
            snapshot['bases'].setdefault(item['role'], {})[item['slot']] = item
        elif line.startswith('layout '):
            snapshot['layouts'][item['role']] = item
        elif line.startswith('pp_rdma '):
            snapshot['pp_rdma'] = item
        elif line.startswith('pp_base '):
            snapshot.setdefault('pp_bases', {})[item['plane']] = item
        elif line.startswith('sample '):
            key = f"{item['role']}/p{item['plane']}/{item['boundary']}"
            require(key not in snapshot['samples'], f'{path}:{number}: duplicate sample {key}')
            if item['status'] == 'saved':
                local = path.parent / PurePosixPath(item['path']).name
                data = local.read_bytes()
                require(len(data) == item['bytes'], f'{local}: expected {item["bytes"]} bytes, got {len(data)}')
                require(item['bytes'] == item['rows']*item['stride'], f'{local}: inconsistent sample size')
                if item['role'] == 'pp_rdma':
                    programmed = snapshot['pp_bases'][item['plane']]
                    require(item['stride'] == int(programmed['stride']), f'{local}: inconsistent PP stride')
                    require(item['row']+item['rows'] <= int(snapshot['pp_rdma']['height'])//2,
                            f'{local}: beyond PP height')
                else:
                    base = snapshot['bases'][item['role']][item['plane']]['virtual']
                    require(item['address'] == base+item['row']*item['stride'], f'{local}: inconsistent sample address')
                    layout = snapshot['layouts'][item['role']]
                    require(item['row']+item['rows'] <= layout['rows_global'], f'{local}: beyond global rows')
                    require(item['stride'] == layout['row_bytes'], f'{local}: inconsistent row stride')
                item['local_file'] = str(local)
                blobs[key] = data
            snapshot['samples'][key] = item
    require(set(snapshot['images']) == set(required_roles), f'{path}: missing requested image metadata')
    for role in required_roles:
        require(set(snapshot['bases'][role]) == {0, 1, 2, 3}, f'{path}: missing four bases for {role}')
        require(all(key in snapshot['images'][role] for key in GEOMETRY), f'{path}: missing six geometry fields')
    # Overlapping samples were read independently; consistency is measured,
    # rather than silently treating duplicate names as independent evidence.
    overlaps = []
    keys = sorted(blobs)
    for index, first in enumerate(keys):
        a = snapshot['samples'][first]
        for second in keys[index+1:]:
            b = snapshot['samples'][second]
            if (a['role'], a['plane']) != (b['role'], b['plane']):
                continue
            begin, end = max(a['address'], b['address']), min(a['address']+a['bytes'], b['address']+b['bytes'])
            if end > begin:
                aa = blobs[first][begin-a['address']:end-a['address']]
                bb = blobs[second][begin-b['address']:end-b['address']]
                overlaps.append({'first': first, 'second': second, 'bytes': end-begin, 'equal': aa == bb})
    snapshot['sample_overlap_checks'] = overlaps
    return snapshot, blobs


def producer_chain(directory, prefix, before, after, old_blobs, new_blobs):
    phases = {'before_SetOpParam': (before, old_blobs), 'after_FinishOperation': (after, new_blobs)}
    for phase in ('after_pool_assignment', 'before_second_PP'):
        path = directory/f'{prefix}-{phase}.txt'
        if path.exists(): phases[phase] = load_snapshot(path, ('output',))
    comparisons = {}
    for first, second in (('after_pool_assignment', 'before_SetOpParam'),
                          ('after_FinishOperation', 'before_second_PP')):
        if first not in phases or second not in phases: continue
        a, aa = phases[first]; b, bb = phases[second]
        require(a['header']['sequence'] == b['header']['sequence'], 'producer sequence differs')
        entries = {}
        for key in sorted(aa.keys() & bb.keys()):
            if not key.startswith('output/'): continue
            old, new = a['samples'][key], b['samples'][key]
            same = all(old[k] == new[k] for k in ('address', 'row', 'rows', 'stride', 'bytes'))
            entries[key] = {'coordinates_equal': same, 'comparable': same,
                'before': stats(aa[key]), 'after': stats(bb[key]),
                'changed_bytes': sum(x != y for x, y in zip(aa[key], bb[key])) if same else None}
        comparisons[f'{first} -> {second}'] = entries
    source_checks = {}
    if 'before_second_PP' in phases:
        pp, pp_bytes = phases['before_second_PP']
        for plane in range(4):
            actual = pp.get('pp_bases', {}).get(plane, {})
            expected = after['bases']['output'][plane]
            entry = {'split_physical': expected['physical'], 'pp_physical': actual.get('physical'),
                     'same_source_physical': expected['physical'] == actual.get('physical'), 'boundaries': {}}
            for boundary in ('first', 'effective_last', 'global_last', 'reserved_tail'):
                key = f'output/p{plane}/{boundary}'; pp_key = f'pp_rdma/p{plane}/{boundary}'
                if key not in new_blobs or pp_key not in pp_bytes: continue
                a, b = after['samples'][key], pp['samples'][pp_key]
                same = entry['same_source_physical'] and all(a[k] == b[k] for k in ('row', 'rows', 'stride', 'bytes'))
                entry['boundaries'][boundary] = {'same_physical_extent': same,
                    'changed_bytes': sum(x != y for x, y in zip(new_blobs[key], pp_bytes[pp_key])) if same else None,
                    'before': stats(new_blobs[key]), 'after': stats(pp_bytes[pp_key])}
            source_checks[str(plane)] = entry
    return {'phases_present': list(phases),
            'saved_bins_read': sum(len(blobs) for _, blobs in phases.values()),
            'saved_bins_by_phase': {p:len(blobs) for p, (_, blobs) in phases.items()},
            'missing_phases': [p for p in ('after_pool_assignment', 'before_second_PP') if p not in phases],
            'snapshots': {p: s for p, (s, _) in phases.items() if p not in ('before_SetOpParam', 'after_FinishOperation')},
            'stage_comparisons': comparisons, 'actual_pp_sources': source_checks,
            'physical_hardware_dma_extent_proved': False}


def stats(data):
    return {'bytes': len(data), 'nonzero_bytes': sum(value != 0 for value in data),
            'all_zero': not any(data), 'first_16_hex': data[:16].hex(), 'last_16_hex': data[-16:].hex()}


def compare(before, after, old_blobs, new_blobs):
    require(before['header']['phase'] == 'before_SetOpParam', 'wrong before phase')
    require(after['header']['phase'] == 'after_FinishOperation', 'wrong after phase')
    for field in ('wrapper', 'sequence', 'boundary'):
        require(before['header'][field] == after['header'][field], f'operation identity differs: {field}')
    require(before['samples'].keys() == after['samples'].keys(), 'before/after sample sets differ')
    metadata = {}
    for role in ('input', 'output'):
        changes = {key: {'before': before['images'][role].get(key), 'after': after['images'][role].get(key)}
                   for key in before['images'][role] if before['images'][role].get(key) != after['images'][role].get(key)}
        metadata[role] = {'before': before['images'][role], 'after': after['images'][role],
                          'changed_fields': changes,
                          'six_geometry_fields_unchanged': all(before['images'][role][k] == after['images'][role][k] for k in GEOMETRY),
                          'four_bases_unchanged': before['bases'][role] == after['bases'][role],
                          'bases_before': before['bases'][role], 'bases_after': after['bases'][role],
                          'layout_before': before['layouts'][role], 'layout_after': after['layouts'][role]}
    results = {}
    for key in sorted(before['samples']):
        a, b = before['samples'][key], after['samples'][key]
        coordinates_equal = all(a.get(k) == b.get(k) for k in ('address', 'row', 'rows', 'stride', 'bytes'))
        entry = {'before_sample': a, 'after_sample': b, 'coordinates_equal': coordinates_equal,
                 'comparable': coordinates_equal and key in old_blobs and key in new_blobs}
        if key in old_blobs:
            entry['before'] = stats(old_blobs[key])
        if key in new_blobs:
            entry['after'] = stats(new_blobs[key])
        if entry['comparable']:
            old, new = old_blobs[key], new_blobs[key]
            changed = [i for i, (x, y) in enumerate(zip(old, new)) if x != y]
            entry.update({'unchanged': not changed, 'changed_bytes': len(changed),
                          'first_changed_byte': changed[0] if changed else None,
                          'last_changed_byte': changed[-1] if changed else None})
            image = before['images'][a['role']]
            payload = None
            if image['ImageFormat'] == 1 and image['RawPackBit'] == 2:
                payload = image['GWidth']*3 // (4 if image['AddrType'] == 2 else 2)
                require(payload <= a['stride'], f'{key}: packed-12 payload exceeds stride')
            entry['packed12_payload_bytes_per_row'] = payload
            entry['row_changes'] = []
            for row in range(a['rows']):
                lo, hi = row*a['stride'], (row+1)*a['stride']
                changed_row = [i-lo for i in changed if lo <= i < hi]
                entry['row_changes'].append({'row': a['row']+row, 'changed_bytes': len(changed_row),
                    'changed_payload_bytes': sum(i < payload for i in changed_row) if payload is not None else None,
                    'changed_padding_bytes': sum(i >= payload for i in changed_row) if payload is not None else None})
        results[key] = entry
    output_tails = [results[f'output/p{plane}/split_reserved_tail'] for plane in range(4)]
    output_ends = [results[f'output/p{plane}/split_effective_last'] for plane in range(4)]
    summary = {
        'saved_bins_read': len(old_blobs)+len(new_blobs),
        'paired_boundaries': len(results),
        'all_boundaries_comparable': all(r['comparable'] for r in results.values()),
        'six_geometry_fields_unchanged': all(m['six_geometry_fields_unchanged'] for m in metadata.values()),
        'four_bases_unchanged': all(m['four_bases_unchanged'] for m in metadata.values()),
        'all_sample_overlaps_consistent': all(check['equal'] for s in (before, after) for check in s['sample_overlap_checks']),
        'output_split_tail_unchanged_all_planes': all(r.get('unchanged', False) for r in output_tails),
        'output_split_tail_zero_before_all_planes': all(r.get('before', {}).get('all_zero', False) for r in output_tails),
        'output_split_tail_zero_after_all_planes': all(r.get('after', {}).get('all_zero', False) for r in output_tails),
        'output_effective_last_changed_bytes_by_plane': [r.get('changed_bytes') for r in output_ends],
        'input_sampled_boundaries_unchanged': all(r.get('unchanged', False) for k, r in results.items() if k.startswith('input/')),
    }
    return {'scope': 'Observed CPU-readable buffers around the actual software MnF split in a QEMU development capture',
            'physical_hardware_dma_extent_proved': False,
            'equality_semantics': 'Equal bytes do not distinguish no write from a write of identical bytes; zero tail provenance is unresolved.',
            'summary': summary, 'metadata_comparison': metadata,
            'snapshots': {'before': before, 'after': after}, 'boundary_comparisons': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=ROOT/'outputs/firmware-runtime/capture-geometry')
    parser.add_argument('--prefix', help='Snapshot prefix; inferred when one split sequence is present')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--run-log', type=Path, help='Actual revision-3 capture stderr log; inferred only if exactly one matches')
    args = parser.parse_args()
    directory = args.directory.resolve()
    candidates = list(directory.glob('*-before_SetOpParam.txt'))
    if not args.prefix and len(candidates) == 1:
        args.prefix = candidates[0].name.removesuffix('-before_SetOpParam.txt')
    before_path = directory/f'{args.prefix}-before_SetOpParam.txt'
    after_path = directory/f'{args.prefix}-after_FinishOperation.txt'
    if before_path.exists() and after_path.exists():
        before, old_blobs = load_snapshot(before_path)
        after, new_blobs = load_snapshot(after_path)
        report = compare(before, after, old_blobs, new_blobs)
        report['producer_chain'] = producer_chain(directory, args.prefix, before, after, old_blobs, new_blobs)
        report['summary']['producer_chain_saved_bins_read'] = report['producer_chain']['saved_bins_read']
    else:
        require(not candidates, 'Several or incomplete split sequences; choose an explicit --prefix')
        report = {'scope': 'Actual runtime trace with no complete split snapshots collected',
                  'summary': {'saved_bins_read': 0, 'paired_boundaries': 0},
                  'boundary_comparisons': {}, 'producer_chain': {'missing_phases': [
                      'after_pool_assignment', 'before_SetOpParam', 'after_FinishOperation', 'before_second_PP']},
                  'physical_hardware_dma_extent_proved': False}
    candidates = list(directory.glob('*strace.log'))
    run_log = args.run_log or (candidates[0] if len(candidates) == 1 else None)
    if run_log:
        lines = run_log.read_text(encoding='utf-8', errors='replace').splitlines()
        markers = {'revision_3_enabled': 'geometry revision=3 loaded enabled=1',
                   'revision_4_enabled': 'geometry revision=4 loaded enabled=1',
                   'pool_assignment': 'geometry pool assigned sequence=',
                   'second_pp_source_matched': 'geometry second PP before start matched=1',
                   'setop_parameter_accepted': 'caller_ok=1 parameter=',
                   'before_snapshot': 'geometry snapshot phase=before_SetOpParam',
                   'software_split': 'EP MnF software Bayer split',
                   'after_snapshot': 'geometry snapshot phase=after_FinishOperation',
                   'finish_paired': 'caller_ok=1 paired=1',
                   'jpeg_assertion': "Assertion `mybuf.size > 0' failed"}
        report['actual_run_log'] = {'file': str(run_log), 'marker_lines': {
            name: [index+1 for index, line in enumerate(lines) if marker in line]
            for name, marker in markers.items()}}
        report['actual_run_log']['accepted_mode17_observed'] = any(
            'geometry gate=SetOpParam' in line and 'caller_ok=1' in line and 'copied=1 mode=17' in line for line in lines)
        report['actual_run_log']['software_split_observed'] = bool(report['actual_run_log']['marker_lines']['software_split'])
    output = args.output or directory/'capture-geometry-analysis.json'
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report['summary'], indent=2))
    for key, result in report['boundary_comparisons'].items():
        print(f'{key}: changed={result.get("changed_bytes")} before_nonzero={result.get("before", {}).get("nonzero_bytes")} after_nonzero={result.get("after", {}).get("nonzero_bytes")}')
    print(output)


if __name__ == '__main__':
    main()
