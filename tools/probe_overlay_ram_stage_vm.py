"""Run the real RAM staging script over the incremental overlay's card tree.

The overlay ships a merged runtime-files.list because mod/deployment/mfw/ram-stage.sh
copies exactly the listed entries into the RAM runtime the services execute from.
Checking that the manifest contains the backend path proves nothing; this probe
applies the overlay to a LUT-mod baseline tree, runs the production staging
script in an ARM guest, and verifies the staged bytes.

Offline: a private VM with a tmpfs RAM target. No camera, no card, no flash.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import zipfile

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''#!/bin/sh
set -eu
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
rm -rf /perf/card /tmp/nx300m-mfw
mkdir -p /perf/card /tmp/nx300m-mfw
cp /perf/ram-stage.sh /perf/card/mfw-ram-stage.sh
chmod 755 /perf/card/mfw-ram-stage.sh
cd /perf/card
/rescue/busybox tar xzf /perf/card-tree.tar.gz
# The overlay is applied exactly as a user would: overwrite/add over the baseline.
/rescue/busybox tar xzf /perf/overlay.tar.gz
man_before=$(cat runtime-files.list | wc -l)
/bin/sh ./mfw-ram-stage.sh /perf/card /tmp/nx300m-mfw > /perf/stage.log 2>&1 || {
    cat /perf/stage.log; exit 1; }
printf 'STAGE_BASELINE_MANIFEST %s\n' "$man_before"
printf 'STAGE_RUNTIME %s\n' /tmp/nx300m-mfw/runtime
ls -l /tmp/nx300m-mfw/runtime/ipcs-diagnostic/lib/smartfilter_ram.bin
ls -l /tmp/nx300m-mfw/runtime/ipcs-diagnostic/bin/smartfilter_loader.arm
ls -l /tmp/nx300m-mfw/runtime/smartfilter-preview.sh
ls -l /tmp/nx300m-mfw/runtime/bin/lut_engine.arm
ls -l /tmp/nx300m-mfw/runtime/bin/nxui.arm
wc -l /perf/card/runtime-files.list
# Every staged entry must match the card byte for byte, and the manifest must
# describe exactly what landed in RAM.
missing=0; differ=0; n=0
while IFS= read -r relative; do
    [ -n "$relative" ] || continue
    n=$((n+1))
    if [ ! -f "/tmp/nx300m-mfw/runtime/$relative" ]; then
        printf 'STAGE_MISSING %s\n' "$relative"; missing=$((missing+1)); continue
    fi
    cmp -s "/perf/card/$relative" "/tmp/nx300m-mfw/runtime/$relative" || {
        printf 'STAGE_DIFFER %s\n' "$relative"; differ=$((differ+1)); }
done < /perf/card/runtime-files.list
printf 'STAGE_CHECKED %s missing=%s differ=%s\n' "$n" "$missing" "$differ"
[ "$missing" = 0 ] && [ "$differ" = 0 ]
sha256sum /tmp/nx300m-mfw/runtime/ipcs-diagnostic/lib/smartfilter_ram.bin
printf 'RAM_STAGE_OVERLAY_PASSED\n'
'''


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--overlay', type=Path,
                        default=ROOT/'outputs/deployment/capture-overlay/nx300m-capture-overlay.zip')
    parser.add_argument('--baseline', type=Path, action='append', default=None,
                        help='installed card layer, applied in order (default: connected kit then LUT-mod increment)')
    parser.add_argument('--from-package', type=Path, default=None,
                        help='verify a deployment package instead of the overlay: apply its files and merge the '
                             'manifest with the same rule deploy() uses')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise SystemExit('Preserve earlier evidence; choose a new output directory')

    package_mode = bool(args.from_package)
    overlay = (args.from_package.resolve() if package_mode else args.overlay.resolve())
    layers = args.baseline or [ROOT/'outputs/deployment/mfw-connected/sd',
                              ROOT/'outputs/deployment/lut-mod/sd']
    layers = [layer.resolve() for layer in layers]
    with zipfile.ZipFile(overlay) as package:
        if package.testzip():
            raise SystemExit('archive CRC failed')
        overlay_files = {name: package.read(name) for name in package.namelist()
                         if name.startswith('nx300m-mod/') and not name.endswith('/')}
    # deploy(): the staged set is the card manifest plus every package file that is
    # not a look, catalogue, state record or markdown document.
    extra = ({name[len('nx300m-mod/'):] for name in overlay_files
              if not name.endswith('.md') and not name.startswith('nx300m-mod/looks/')
              and not name.startswith('nx300m-mod/.state/') and name != 'nx300m-mod/looks.txt'}
             if package_mode else None)
    manifest = (overlay_files['nx300m-mod/runtime-files.list'].decode().splitlines()
                if not package_mode else None)
    ram = overlay_files['nx300m-mod/ipcs-diagnostic/lib/smartfilter_ram.bin']

    # Card tree as an installed runtime carries it (each layer applied in order),
    # plus the overlay on top.
    card = {}
    for layer in layers:
        layer_root = layer/'nx300m-mod'
        for path in layer_root.rglob('*'):
            if path.is_file():
                card[path.relative_to(layer_root).as_posix()] = path.read_bytes()
    applied = dict(card)
    for name, data in overlay_files.items():
        applied[name[len('nx300m-mod/'):]] = data
    if package_mode:
        # deploy() merges the card manifest with the package's runtime files.
        card_manifest = [line.strip() for line in
                         card['runtime-files.list'].decode('ascii').splitlines() if line.strip()]
        manifest = sorted(set(card_manifest) | extra)
        applied['runtime-files.list'] = ('\n'.join(manifest) + '\n').encode('ascii')

    import io
    import tarfile

    def tarball(files):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith(('.sh', '.arm')) else 0o644
                tar.addfile(info, io.BytesIO(data))
        return raw.getvalue()

    vm.OUT = out
    vm.PROBE = PROBE
    vm.EXTRA_FILES = {
        'perf/card-tree.tar.gz': tarball(card),
        'perf/overlay.tar.gz': tarball({name[len('nx300m-mod/'):]: data
                                        for name, data in overlay_files.items()}
                                       | {'runtime-files.list': applied['runtime-files.list']}),
        'perf/ram-stage.sh': (ROOT/'mod/deployment/mfw/ram-stage.sh').read_bytes(),
    }
    vm.prepare()

    report = {
        'passed': False,
        'scope': 'Production ram-stage.sh over the overlay manifest; tmpfs RAM target in a private ARM guest',
        'overlay': str(overlay),
        'overlay_sha256': sha_bytes(overlay.read_bytes()),
        'baseline_layers': [str(layer) for layer in layers],
        'baseline_files': len(card),
        'overlay_files': len(overlay_files),
        'manifest_entries': len(manifest),
        'backend_sha256': sha_bytes(ram),
        'manifest_expects_backend': 'ipcs-diagnostic/lib/smartfilter_ram.bin' in manifest,
        'applied_tree_files': len(applied),
    }
    started = False
    try:
        vm.call('start')
        started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            log = out/'serial.log'
            if log.exists() and 'MENU_PERF_VM_READY' in log.read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('ram stage VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh', timeout=300)
        arm_vm.write_json(out/'execution.json', execution)
        text = execution['output']
        report['exit_code'] = execution['exit_code']
        report['checked'], report['missing'], report['differ'] = None, None, None
        for line in text.splitlines():
            if line.startswith('STAGE_CHECKED'):
                report['checked'] = int(line.split()[1])
                report['missing'] = int(line.split()[2].split('=')[1])
                report['differ'] = int(line.split()[3].split('=')[1])
            if line.startswith('STAGE_MISSING') or line.startswith('STAGE_DIFFER'):
                report.setdefault('staged_mismatches', []).append(line)
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and len(parts[0]) == 64 and parts[1].endswith('smartfilter_ram.bin'):
                report['staged_backend_sha256'] = parts[0]
        report['passed'] = (execution['exit_code'] == 0
                            and 'RAM_STAGE_OVERLAY_PASSED' in text
                            and report.get('staged_backend_sha256') == sha_bytes(ram)
                            and report.get('missing') == 0 and report.get('differ') == 0)
        for name in ('stage.log',):
            try:
                collect(out, name)
            except Exception:
                pass
    finally:
        if started:
            try:
                vm.call('stop')
            except Exception as error:
                report['stop_error'] = repr(error)[:300]
            (out/'initramfs.cpio.gz').unlink(missing_ok=True)
    arm_vm.write_json(out/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
