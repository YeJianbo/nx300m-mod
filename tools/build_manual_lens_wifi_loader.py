"""Stage a single-module rootfs launch candidate; never install or run it."""
import json
from pathlib import Path
import subprocess

from build_stock_app_loader import ROOT, persistent_launch_files, verify_native

OUT = ROOT / 'outputs/controls/manual-lens/loader-candidate'
HELPER = ROOT / 'mod/deployment/app-loader/preload-manual-lens-wifi.sh'


def build():
    helper = HELPER.read_bytes()
    if b'\r' in helper:
        raise ValueError('Shell helper must use LF')
    library = ROOT / 'outputs/controls/manual-lens/manual-lens-wifi.so'
    native = verify_native(library)
    changes, scripts, originals = [], [], []
    for item in persistent_launch_files():
        name = item['destination'].lstrip('/')
        content = item['content']
        source = item['source']
        if name == 'usr/lib/nx300m-mod/preload.sh':
            content = helper
            source = HELPER.relative_to(ROOT).as_posix()
        elif name == 'usr/lib/nx300m-mod/launch-profile':
            # Explicit activation belongs to this candidate, not the full mod.
            content = b'manual-lens-wifi\n'
            source = 'generated: manual-lens-wifi profile'
        target = OUT / 'rootfs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if target.read_bytes() != content:
            raise ValueError('Candidate readback differs: ' + name)
        changes.append({'destination': item['destination'], 'source': source,
                        'change': item['change'], 'metadata': item['metadata'],
                        'bytes': len(content)})
        if 'original' in item:
            original = OUT / 'original' / name
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(item['original'])
            originals.append(name)
        if name.endswith(('.sh', 'nx300m-camera-launch')):
            scripts.append(target)
    name = 'usr/lib/nx300m-mod/manual-lens-wifi.so'
    (OUT / 'rootfs' / name).write_bytes(native)
    changes.append({'destination': '/' + name, 'source': library.relative_to(ROOT).as_posix(),
                    'change': 'new', 'bytes': len(native),
                    'metadata': {'mode': '0o644', 'uid': 0, 'gid': 0}})
    expected = {item['destination'].lstrip('/') for item in changes}
    actual = {p.relative_to(OUT / 'rootfs').as_posix()
              for p in (OUT / 'rootfs').rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('Candidate contains unexpected or missing rootfs files')
    checks = []
    for path in scripts:
        wsl_path = '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]
        result = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', '/bin/sh', '-n', wsl_path],
                                capture_output=True, text=True)
        checks.append({'path': path.relative_to(ROOT).as_posix(), 'exit_code': result.returncode,
                       'stderr': result.stderr})
        if result.returncode:
            raise RuntimeError('Shell syntax failed: ' + json.dumps(checks[-1]))
    report = {
        'status': 'offline rootfs overlay only; not an installer or firmware',
        'rootfs_changes': changes, 'module_count': 1,
        'profiles': ['stock', 'manual-lens-wifi'], 'default_profile': 'manual-lens-wifi',
        'shell_syntax': checks, 'retained_stock_launch_inputs': originals,
        'unchanged_by_overlay': ['/usr/bin/di-camera-app-nx300', '/usr/lib/libmisc.so'],
        'installed_mfw_preservation': 'overlay excludes libmisc and camera ELF; full installed rootfs compatibility not verified',
        'stock_launch_file_precondition': 'hib2.sh and camera desktop must match retained originals before any future merge; do not overwrite installed differences',
        'activation': 'new app exec through reviewed launch sites; autoexec alone and existing snapshot do not activate',
        'disabled_or_unknown_profile': 'original app with inherited environment at next exec; no hot unload',
        'inherited_preload': 'preserved; module_count counts project libraries added by this candidate',
        'firmware_packaged': False, 'camera_written': False, 'snapshot_modified': False,
        'physical_wifi_verified': False,
        'remaining': ['installed launch file comparison', 'verified fresh-exec and snapshot procedure',
                      'internal installation authorization and recovery route', 'physical RVF and phone connection'],
    }
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    result = build()
    print(json.dumps({'status': result['status'], 'module_count': result['module_count'],
                      'shell_checks': len(result['shell_syntax']), 'output': str(OUT)}, ensure_ascii=False))
