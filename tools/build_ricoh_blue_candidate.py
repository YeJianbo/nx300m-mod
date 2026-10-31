"""Create separate, subjective blue-preserving NX300 DCP candidates.

Original installed profiles are read-only. Only positive LookTable HueShift in
the specified blue region is attenuated; all other profile tags are preserved.
This is a style adjustment, not Ricoh calibration or an Exact reproduction.
"""
import argparse
import json
import math
import os
from pathlib import Path
import struct

from adobe_profiles import DcpProfile

PROJECT = Path(__file__).resolve().parents[1]


def smoothstep(low, high, value):
    value = min(1.0, max(0.0, (value-low)/(high-low)))
    return value*value*(3-2*value)


def blue_weight(hue, saturation):
    hue_weight = smoothstep(190, 215, hue)*(1-smoothstep(255, 280, hue))
    return hue_weight*smoothstep(.08, .25, saturation)


def patch_payload(data, endian, tag, kind, count, payload):
    directory = struct.unpack_from(endian+'I', data, 4)[0]
    entries = struct.unpack_from(endian+'H', data, directory)[0]
    for index in range(entries):
        entry = directory+2+12*index
        found = struct.unpack_from(endian+'H', data, entry)[0]
        if found != tag:
            continue
        if len(data)%4:
            data.extend(b'\0'*(4-len(data)%4))
        pointer = len(data)
        data.extend(payload)
        # Preserve the original payload. Redirect only this IFD entry to the
        # appended payload, keeping unrelated tags and offsets untouched.
        struct.pack_into(endian+'HII', data, entry+2, kind, count, pointer)
        return list(range(entry+2, entry+12))
    raise ValueError(f'Missing tag {tag}')


def build(source, destination, new_name, strength):
    before = DcpProfile(source)
    dims = before.get(50981)
    if not dims or len(dims) != 3 or before.tags[50982][0] != 11:
        raise ValueError('Expected a 3D floating-point DCP LookTable')
    hue_count, sat_count, val_count = map(int, dims)
    if hue_count < 2 or sat_count < 2 or val_count < 1:
        raise ValueError('Invalid LookTable dimensions')
    original = before.get(50982)
    if len(original) != hue_count*sat_count*val_count*3:
        raise ValueError('LookTable length differs from dimensions')
    changed = list(original)
    changed_nodes = 0
    maximum_reduction = 0.0
    for value in range(val_count):
        for hue in range(hue_count):
            for saturation in range(sat_count):
                index = ((value*hue_count+hue)*sat_count+saturation)*3
                weight = blue_weight(hue*360/hue_count, saturation/(sat_count-1))
                reduction = strength*weight*max(original[index], 0)
                changed[index] -= reduction
                if reduction:
                    changed_nodes += 1
                    maximum_reduction = max(maximum_reduction, reduction)
    payload = struct.pack(before.endian+'f'*len(changed), *changed)
    data = bytearray(before.data)
    modified_prefix_bytes = patch_payload(data, before.endian, 50982, 11, len(changed), payload)
    name_payload = new_name.encode('ascii')+b'\0'
    modified_prefix_bytes += patch_payload(data, before.endian, 50936, 2, len(name_payload), name_payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        raise ValueError('Candidate must not overwrite its source')
    destination.write_bytes(data)
    after = DcpProfile(destination)
    if before.tags.keys() != after.tags.keys():
        raise ValueError('Tag set changed')
    for tag in before.tags:
        if tag not in (50936, 50982) and before.tags[tag] != after.tags[tag]:
            raise ValueError(f'Unrelated tag changed: {tag}')
    allowed = set(modified_prefix_bytes)
    if any(a != b and index not in allowed for index, (a, b) in enumerate(zip(before.data, data))):
        raise ValueError('Unrelated source prefix bytes changed')
    packed = after.get(50982)
    protected_nodes = 0
    for value in range(val_count):
        for hue in range(hue_count):
            for saturation in range(sat_count):
                index = ((value*hue_count+hue)*sat_count+saturation)*3
                if packed[index+1:index+3] != original[index+1:index+3]:
                    raise ValueError('Saturation or value scale changed')
                weight = blue_weight(hue*360/hue_count, saturation/(sat_count-1))
                if weight == 0 or original[index] <= 0 or strength == 0:
                    protected_nodes += 1
                    if packed[index] != original[index]:
                        raise ValueError('Neutral, non-blue or non-positive hue shift changed')
                if packed[index] > original[index]+1e-6:
                    raise ValueError('Hue shift increased')
    if source.read_bytes() != before.data:
        raise ValueError('Original source changed during build')
    return {'source': str(source), 'candidate': str(destination),
            'source_name': before.get(50936), 'candidate_name': after.get(50936),
            'strength': strength, 'dimensions': dims,
            'changed_hue_nodes': changed_nodes,
            'maximum_hue_reduction_degrees': maximum_reduction,
            'protected_nodes_verified': protected_nodes,
            'sat_scale_and_val_scale_identical': True,
            'all_other_tags_identical': True,
            'source_prefix_unchanged_except_two_ifd_entries': True,
            'source_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strength', type=float, default=1)
    parser.add_argument('--output-dir', type=Path,
                        default=PROJECT/'outputs/looks/ricoh-blue-candidate')
    args = parser.parse_args()
    if not math.isfinite(args.strength) or not 0 <= args.strength <= 1:
        parser.error('--strength must be finite and in [0,1]')
    destination = args.output_dir.resolve()
    source_dir = Path(os.environ['APPDATA'])/'Adobe/CameraRaw/CameraProfiles/Imported/NX300_DCP_Camera_Profiles_v1'
    profiles, reports = [], []
    for style in ('Positive', 'Negative'):
        key = style.lower()+'-blue-adjusted'
        name = 'NX300 GR IV '+style+' Film Blue Adjusted'
        filename = name+'.dcp'
        source = source_dir/('NX300 GR IV '+style+' Film.dcp')
        if DcpProfile(source).get(50936) != 'NX300 GR IV '+style+' Film Exact':
            raise ValueError('Unexpected original profile identity')
        reports.append(build(source, destination/filename, name, args.strength))
        profiles.append({'key': key, 'label': name, 'filename': filename})
    manifest = {'scope': 'Subjective blue-preserving style candidates, not Ricoh calibration or Exact reproduction',
                'profiles': profiles}
    (destination/'profiles.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    report = {'scope': manifest['scope'], 'strength': args.strength,
              'rule': {'hue_domain': 'ProPhoto HSV degrees',
                       'hue_weight': 'smoothstep(190,215,h) * (1-smoothstep(255,280,h))',
                       'saturation_weight': 'smoothstep(0.08,0.25,s)',
                       'modified_component': 'positive LookTable HueShift only',
                       'formula': 'HueShift -= strength * hue_weight * saturation_weight * max(HueShift,0)',
                       'sampling_note': 'Weights are sampled at native DCP nodes. Unchanged neutral/non-blue nodes are verified; interpolation can extend the transition slightly beyond the continuous weight thresholds.'},
              'profiles': reports, 'passed': True}
    (destination/'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
