"""Render read-only NX300M RAW/DCP references with WSL RawTherapee 5.11.

This is an offline diagnostic renderer, not the Samsung ISP or Lightroom.
The CLI starts from neutral parameters; neither user defaults nor RAW sidecars
are loaded. All effective parameters and renderer output are retained.
"""
import argparse
import configparser
import io
import json
import os
from pathlib import Path
import subprocess
import time

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageStat

from adobe_profiles import DcpProfile

PROJECT = Path(__file__).resolve().parents[1]


def linux_path(path):
    path = Path(path).resolve()
    if not path.drive or len(path.drive) != 2:
        raise ValueError(f"Expected a local Windows drive: {path}")
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def wsl(*args, **kwargs):
    check = kwargs.pop('check', True)
    return subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '--exec',
                           *args], text=True, encoding='utf-8', errors='replace',
                          check=check, **kwargs)


def pp3(profile, *, apply_look_table=True):
    return f"""[Version]
AppVersion=5.11
Version=351

[Exposure]
Auto=false
Compensation=0
Brightness=0
Contrast=0
Saturation=0
Black=0
HighlightCompr=0
ShadowCompr=0
HistogramMatching=false
CurveFromHistogramMatching=false
Curve=0;
Curve2=0;

[HLRecovery]
Enabled=false

[White Balance]
Enabled=true
Setting=Camera
Equal=1
TemperatureBias=0

[Color Management]
InputProfile=file:{linux_path(profile)}
ToneCurve=true
ApplyLookTable={str(apply_look_table).lower()}
ApplyBaselineExposureOffset=true
ApplyHueSatMap=true
DCPIlluminant=0
WorkingProfile=ProPhoto
WorkingTRC=none
OutputProfile=RTv4_sRGB
OutputProfileIntent=Relative
OutputBPC=false

[RAW Bayer]
Method=amaze

[Sharpening]
Enabled=false

[PostDemosaicSharpening]
Enabled=false

[Directional Pyramid Denoising]
Enabled=false

[Film Simulation]
Enabled=false

[Crop]
Enabled=false

[Resize]
Enabled=false
"""


def inspect_image(path):
    with Image.open(path) as source:
        source.load()
        image = source.convert('RGB')
        stats = ImageStat.Stat(image)
        icc = source.info.get('icc_profile')
        description = (ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(
            io.BytesIO(icc))).strip() if icc else None)
        result = {'path': str(path), 'size': list(source.size),
                  'mode': source.mode, 'orientation': source.getexif().get(274, 1),
                  'rgb_mean_8bit': stats.mean,
                  'rgb_extrema_8bit': image.getextrema(),
                  'icc_bytes': len(icc or b''), 'icc_description': description}
        if min(stats.mean) < 1 or max(stats.stddev) < 1 or not icc:
            raise ValueError(f"Black/flat or untagged output: {result}")
        return result, icc


def check_effective(path, profile, *, apply_look_table=True):
    config = configparser.ConfigParser(interpolation=None)
    config.read(path, encoding='utf-8')
    color = config['Color Management']
    for key, expected in {'ToneCurve': True, 'ApplyLookTable': apply_look_table,
                          'ApplyBaselineExposureOffset': True,
                          'ApplyHueSatMap': True}.items():
        if color.getboolean(key) != expected:
            raise ValueError(f"Unexpected DCP stage setting: {key}, expected {expected}")
    if color['InputProfile'] != 'file:' + linux_path(profile):
        raise ValueError('Effective DCP path differs')
    if config['White Balance']['Setting'] != 'Camera':
        raise ValueError('Effective white balance is not Camera')
    if config['Exposure'].getboolean('Auto') or config['Exposure'].getboolean('HistogramMatching'):
        raise ValueError('Automatic rendering adjustment enabled')
    if float(config['Exposure']['Compensation']) != 0:
        raise ValueError('Additional exposure compensation enabled')
    return {section: dict(config[section]) for section in
            ('White Balance', 'Exposure', 'Color Management', 'RAW Bayer')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=PROJECT/'outputs/looks/ricoh-reference')
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--profiles', nargs='+',
                        help='Render only these profile keys, in the specified order')
    parser.add_argument('--profile-dir', type=Path,
                        help='Use explicitly named DCP candidates from this directory/profiles.json')
    parser.add_argument('--without-look-table', action='store_true',
                        help='Diagnostic render: disable only the DCP LookTable stage')
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    reference = json.loads((PROJECT/'outputs/looks/photo-reference.json').read_text(encoding='utf-8'))
    raw = (args.raw or Path(reference['raw']['path'])).resolve()
    if not raw.is_file():
        raise FileNotFoundError(raw)
    profiles_root = Path(os.environ['APPDATA'])/'Adobe/CameraRaw/CameraProfiles/Imported/NX300_DCP_Camera_Profiles_v1'
    profiles = [('adobe-standard', 'NX300 Adobe Standard', Path('./local/lightroom/Resources/CameraProfiles/Adobe Standard/Samsung NX300 Adobe Standard.dcp')),
                ('positive-exact', 'NX300 GR IV Positive Film Exact', profiles_root/'NX300 GR IV Positive Film.dcp'),
                ('negative-exact', 'NX300 GR IV Negative Film Exact', profiles_root/'NX300 GR IV Negative Film.dcp')]
    profile_group = None
    if args.profile_dir:
        profile_dir = args.profile_dir.resolve()
        profile_group = json.loads((profile_dir/'profiles.json').read_text(encoding='utf-8'))
        profiles = []
        for item in profile_group['profiles']:
            profile_path = (profile_dir/item['filename']).resolve()
            if profile_path.parent != profile_dir:
                raise ValueError('Candidate DCP must be directly inside --profile-dir')
            profiles.append((item['key'], item['label'], profile_path))
    if args.profiles:
        available = {item[0]: item for item in profiles}
        unknown = set(args.profiles)-available.keys()
        if unknown:
            parser.error('Unknown profile keys: '+', '.join(sorted(unknown)))
        profiles = [available[key] for key in dict.fromkeys(args.profiles)]
    if not profiles:
        parser.error('No profiles selected')
    apply_look_table = not args.without_look_table
    # Separate config/cache directories ensure the CLI cannot read the user's
    # own defaults. Omitting -d and -s also excludes defaults and RAW sidecars.
    for folder in ('xdg-config', 'xdg-cache'):
        (destination/folder).mkdir(exist_ok=True)
    env_args = ['env', 'XDG_CONFIG_HOME='+linux_path(destination/'xdg-config'),
                'XDG_CACHE_HOME='+linux_path(destination/'xdg-cache'),
                'OMP_NUM_THREADS=4', 'LC_ALL=C.UTF-8']
    # The upstream help action intentionally returns 255, even on success.
    help_result = wsl(*env_args, 'rawtherapee-cli', '-h', capture_output=True, check=False)
    if 'RawTherapee, version 5.11' not in help_result.stdout:
        raise RuntimeError(help_result.stderr or help_result.stdout)
    version = help_result.stdout.splitlines()[0]
    package = wsl('dpkg-query', '-W', 'rawtherapee', capture_output=True).stdout.strip()
    report = {'renderer': version, 'debian_package': package, 'raw': str(raw),
              'apply_look_table': apply_look_table,
              'selected_profiles': [item[0] for item in profiles],
              'profile_group': profile_group,
              'diagnostic_ablation': 'DCP LookTable disabled; other stages retained' if args.without_look_table else None,
              'scope': 'RawTherapee offline RAW + camera matrices, HueSatMap, LookTable, exposure offset and tone reference; not Lightroom or Samsung ISP equivalence',
              'processing_notes': [
                  'Adobe Standard has no stored ToneCurve; RawTherapee adds its built-in Adobe default curve for Adobe copyright profiles.',
                  'Exact profiles use their stored 128-point ToneCurve and +0.5EV BaselineExposureOffset.',
                  'RawTherapee implements the exposure offset as a multiplicative gain before LookTable/ToneCurve; Adobe and Samsung pixel equivalence is not claimed.',
                  'RawTherapee does not parse DefaultBlackRender; extra black, shadow, histogram matching and automatic exposure adjustments are explicitly disabled.',
                  'RawTherapee RAW active bounds may differ from the embedded camera JPEG; do not interpret direct pixel differences as color error.'],
              'neutral_cli_no_defaults_or_sidecars': True, 'renders': []}
    iccs = []
    for key, label, profile in profiles:
        parsed = DcpProfile(profile)
        expected = ('Adobe Standard' if key == 'adobe-standard' else label)
        if parsed.get(50708) != 'Samsung NX300' or parsed.get(50936) != expected:
            raise ValueError(f'Unexpected DCP identity: {parsed.metadata()}')
        settings = destination/(key+'.pp3')
        settings.write_text(pp3(profile, apply_look_table=apply_look_table), encoding='utf-8')
        output = destination/(key+'.png')
        command = [*env_args, 'rawtherapee-cli', '-q', '-Y', '-n', '-b8',
                   '-O', linux_path(output), '-p', linux_path(settings), '-c', linux_path(raw)]
        started = time.monotonic()
        with (destination/(key+'.log')).open('w', encoding='utf-8') as log:
            log.write('argv: '+json.dumps(command, ensure_ascii=False)+'\n')
            log.flush()
            wsl(*command, stdout=log, stderr=subprocess.STDOUT)
        effective = check_effective(Path(str(output)+'.out.pp3') if Path(str(output)+'.out.pp3').exists()
                                    else Path(str(output)+'.pp3'), profile,
                                    apply_look_table=apply_look_table)
        image_info, icc = inspect_image(output)
        iccs.append(icc)
        report['renders'].append({'key': key, 'label': label, 'dcp': parsed.metadata(),
                                  'effective': effective, 'image': image_info,
                                  'tone_curve_source': ('RawTherapee Adobe default fallback' if key == 'adobe-standard' else 'DCP stored ProfileToneCurve'),
                                  'elapsed_seconds': time.monotonic()-started})
        with Image.open(output) as image:
            image.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
            image.save(destination/(key+'-preview.jpg'), quality=95, subsampling=0, icc_profile=icc)
        print(key, image_info['size'], image_info['rgb_mean_8bit'], flush=True)
    sizes = {tuple(item['image']['size']) for item in report['renders']}
    orientations = {item['image']['orientation'] for item in report['renders']}
    if len(sizes) != 1 or len(orientations) != 1 or any(icc != iccs[0] for icc in iccs):
        raise ValueError('Reference outputs do not share size, orientation and ICC')
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 21)
    cell_width = 720
    cell_height = round(cell_width*next(iter(sizes))[1]/next(iter(sizes))[0])
    sheet = Image.new('RGB', (cell_width*len(profiles), cell_height+65), '#181818')
    draw = ImageDraw.Draw(sheet)
    for index, (key, label, _) in enumerate(profiles):
        with Image.open(destination/(key+'.png')) as image:
            image = image.resize((cell_width, cell_height), Image.Resampling.LANCZOS)
            sheet.paste(image, (cell_width*index, 65))
        draw.text((cell_width*index+16, 9), label.replace('NX300 ', ''), font=font, fill='white')
        stage_label = 'DCP stages' if apply_look_table else 'DCP LookTable OFF'
        draw.text((cell_width*index+16, 35), 'RawTherapee 5.11 / camera WB / '+stage_label,
                  font=font, fill='#bbbbbb')
    sheet.save(destination/'comparison.jpg', quality=95, subsampling=0, icc_profile=iccs[0])
    report['consistent_size_orientation_icc'] = True
    report['passed'] = True
    (destination/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(destination/'report.json', flush=True)


if __name__ == '__main__':
    main()
