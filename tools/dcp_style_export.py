"""Export the post-calibration DCP style stage in linear ProPhoto D50.

This cube takes unrendered working RGB, not an already rendered camera JPEG.
It is kept outside the camera LUT library until its input stage is available.
"""
import argparse
import json
from pathlib import Path

from adobe_profiles import DcpProfile, DcpStyleStage


def export(profile_path, output, size=33):
    if not 2 <= size <= 65:
        raise ValueError('Cube size must be 2..65')
    profile = DcpProfile(profile_path)
    stage = DcpStyleStage(profile)
    output = Path(output)
    if output.suffix.lower() != '.cube':
        raise ValueError('Output must be a .cube file')
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(output.suffix+'.tmp')
    try:
        with pending.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('# DCP post-calibration style stage\n')
            stream.write('# Input/output: linear ProPhoto D50; not camera JPEG/sRGB\n')
            stream.write(f'LUT_3D_SIZE {size}\n')
            for b in range(size):
                for g in range(size):
                    for r in range(size):
                        value = stage.apply_prophoto((r/(size-1), g/(size-1), b/(size-1)))
                        stream.write(' '.join(f'{x:.9f}' for x in value)+'\n')
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    report = {
        'source': str(Path(profile_path).resolve()),
        'source_profile': profile.get(50936, Path(profile_path).stem),
        'cube': str(output.resolve()), 'size': size,
        'input_space': 'linear-prophoto-d50', 'output_space': 'linear-prophoto-d50',
        'stage': stage.metadata(),
        'camera_library_updated': False,
        'samples': {name: {'input': rgb, 'output': stage.apply_prophoto(rgb)}
                    for name, rgb in {'black': (0.,0.,0.), 'gray18': (.18,.18,.18),
                                      'white': (1.,1.,1.), 'blue': (0.,0.,1.),
                                      'cyan': (0.,1.,1.)}.items()},
    }
    output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False,
        indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--size', type=int, default=33)
    args = parser.parse_args()
    print(json.dumps(export(args.profile, args.output, args.size), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
