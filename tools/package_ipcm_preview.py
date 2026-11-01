"""Package the manually enabled IPCM candidate and the exact ARM-tested code."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from build_lut_live_loader import wsl
from fit_ipcm_preview import read_profile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/deployment/ipcm-preview'


def main():
    build = json.loads((OUT/'build-report.json').read_text('utf-8'))
    tested = json.loads((OUT/'arm-vm/report.json').read_text('utf-8'))
    if build['build_exit_code'] or not tested.get('passed') or not tested.get('private_vm_stopped'):
        raise ValueError('Current build and private ARM VM must pass before packaging')
    for name, staged in [('ipcm_preview_loader.arm', 'bin/ipcm_preview_loader.arm'),
                         ('ipcm_preview_blob.bin', 'ipcm_preview_blob.bin')]:
        if (OUT/name).read_bytes() != (OUT/'arm-vm/source-files'/staged).read_bytes():
            raise ValueError('Current binary differs from the ARM-tested code: '+name)
    files = {
        'bin/ipcm_preview_loader.arm': (OUT/'ipcm_preview_loader.arm').read_bytes(),
        'lib/ipcm_preview_blob.bin': (OUT/'ipcm_preview_blob.bin').read_bytes(),
    }
    for name in ('ipcm-preview.sh', 'lut-with-preview.sh'):
        source = ROOT/'mod/deployment/mfw'/name
        subprocess.run(['wsl.exe', '-d', 'Debian', '-e', '/bin/sh', '-n', wsl(source)], check=True)
        files[name] = source.read_text('utf-8').encode('utf-8')
    profiles = []
    for directory in sorted((OUT/'profiles').iterdir()):
        if not directory.is_dir():
            continue
        read_profile(directory/'profile.bin')
        metadata = json.loads((directory/'profile.json').read_text('utf-8'))
        source = ROOT/'looks'/directory.name/'source.cube'
        if hashlib.sha256(source.read_bytes()).hexdigest() != metadata['source_sha256']:
            raise ValueError('Source LUT changed after compilation: '+directory.name)
        metadata['source'] = 'source.cube'
        prefix = 'looks/'+directory.name+'/'
        files[prefix+'preview-ipcm.bin'] = (directory/'profile.bin').read_bytes()
        files[prefix+'preview-ipcm.json'] = (json.dumps(metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
        profiles.append(directory.name)
    if not profiles:
        raise ValueError('No preview profiles available')
    probes=OUT/'pcse-calibration'
    manifest=json.loads((probes/'probes.json').read_text('utf-8'))
    for name in ['baseline.bin']+[item['file'] for item in manifest['profiles']]:
        if Path(name).name!=name:raise ValueError('Invalid calibration profile name')
        model=read_profile(probes/name)
        if 'pcse_delta' not in model:raise ValueError('Calibration requires v3 profile: '+name)
        files['calibration/pcse/'+name]=(probes/name).read_bytes()
    files['calibration/pcse/probes.json']=(probes/'probes.json').read_bytes()
    files['IPCM_PREVIEW.md'] = (ROOT/'docs/IPCM_PREVIEW.md').read_bytes()
    files['IPCM_PCSE.md']=(ROOT/'docs/IPCM_PCSE.md').read_bytes()
    candidate = OUT/'sd/nx300m-mod'
    for name, content in files.items():
        target = candidate/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    archive = OUT/'nx300m-ipcm-preview-candidate.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(files.items()):
            package.writestr('nx300m-mod/'+name, content)
    with zipfile.ZipFile(archive) as package:
        for name, content in files.items():
            if package.read('nx300m-mod/'+name) != content:
                raise ValueError('ZIP content differs: '+name)
    report = {
        'archive': archive.relative_to(ROOT).as_posix(),
        'type': 'manual preview overlay for existing MFW 1.15 LUT installation',
        'profiles': profiles,
        'build': build['build'],
        'arm_vm_passed': tested['passed'],
        'native_color_model_verified': False,
        'pcse_runtime': True,
        'pcse_probe_profiles': len(manifest['profiles'])+1,
        'pcse_fitted_looks': [],
        'automatic_boot_or_menu_integration': False,
        'still_lut_code_replaced': False,
        'camera_or_card_accessed': False,
        'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(files.items())},
    }
    (OUT/'candidate.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'files'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
