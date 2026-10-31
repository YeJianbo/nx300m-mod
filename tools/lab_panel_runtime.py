"""Prepare a complete SD panel in an already private QEMU-user mount namespace.

Run with host Linux Python after guest /tmp is mounted. Bind mounts affect only
the caller's namespace. No candidate binary, stock file or global binfmt changes.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess


def arm_elf(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as stream:
        header = stream.read(20)
    return header[:5] == b'\x7fELF\x01' and header[18:20] == b'\x28\x00'


def prepare(guest, bundle):
    """Copy and mount the panel; caller must already own a private namespace."""
    guest, bundle = Path(guest).resolve(), Path(bundle).resolve()
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeError('Run as Linux root inside the selected private mount namespace')
    if not (bundle/'panel.sh').is_file() or not (bundle/'bin/nxui.arm').is_file():
        raise ValueError('A complete SD bundle with panel.sh and bin/nxui.arm is required')
    owner = int((guest/'var/run/nx300m-firmware-mod-ready').read_text().strip())
    proc = guest/'proc'/str(owner)
    command = (proc/'cmdline').read_bytes().split(b'\0')
    executable = os.readlink(proc/'exe')
    camera_namespace, worker_namespace = os.readlink(proc/'ns/pid'), os.readlink('/proc/self/ns/pid')
    if (owner <= 1 or b'/usr/bin/di-camera-app-nx300' not in command or
            not executable.endswith('/qemu-arm-static') or camera_namespace != worker_namespace):
        raise RuntimeError(f'Firmware owner is not this namespace\'s stock QEMU camera: '
                           f'pid={owner}, exe={executable}, argv={command!r}, '
                           f'camera_namespace={camera_namespace}, worker_namespace={worker_namespace}')
    readiness = (guest/'var/run/nx300m-firmware-mod-ready').stat()
    lock = guest/'var/run/nx300m-firmware-mod-lock'
    if lock.exists():
        os.chown(lock, readiness.st_uid, readiness.st_gid)
    work = guest/'tmp/nx300m-panel-runtime'
    work.mkdir()  # One preparation per session; do not stack private bind mounts.
    panel, system_bin = work/'mod', work/'system-bin'
    if (guest/'mod').is_dir():
        shutil.copytree(guest/'mod', panel, symlinks=True)
    shutil.copytree(bundle, panel, symlinks=True, dirs_exist_ok=True)
    launcher = panel/'panel.sh'
    text = launcher.read_text()
    start, end = text.index('camera_pid() {'), text.index('\nlive=0')
    # This is exclusively the lab copy. Pin the current firmware owner and
    # verify executable, target argv and live readiness for each panel launch.
    check = f'''camera_pid() {{
    [ "$1" = "{owner}" ] || return 1
    [ "$(cat /var/run/nx300m-firmware-mod-ready 2>/dev/null)" = "$1" ] || return 1
    kill -0 "$1" 2>/dev/null || return 1
    executable=$(readlink "/proc/$1/exe" 2>/dev/null) || return 1
    case $executable in */qemu-arm-static) ;; *) return 1 ;; esac
    tr '\\000' '\\n' < "/proc/$1/cmdline" | grep -Fx '/usr/bin/di-camera-app-nx300' >/dev/null
}}
'''
    launcher.write_text(text[:start]+check+text[end:])
    shutil.copytree(guest/'bin', system_bin, symlinks=True)
    interpreter_bin = work/'interpreter'
    interpreter_bin.mkdir()
    shutil.copyfile(guest/'bin/busybox', interpreter_bin/'busybox')
    (interpreter_bin/'busybox').chmod(0o755)
    (interpreter_bin/'sh').symlink_to('busybox')
    prefix = '#!/qemu-arm-static /tmp/nx300m-panel-runtime/interpreter/sh\n'

    def wrapper(path, command):
        if path.is_symlink():
            path.unlink()
        path.write_text(prefix+'exec /qemu-arm-static '+command+' "$@"\n')
        path.chmod(0o755)

    # Preserve stock programs/scripts. Only BusyBox links are applet wrappers;
    # standalone ARM programs retain their own bytes and QEMU dispatch.
    wrapped = []
    for path in list(system_bin.iterdir()):
        if path.is_symlink() and os.readlink(path) in ('busybox', '/bin/busybox'):
            wrapper(path, '/tmp/nx300m-panel-runtime/interpreter/busybox '+shlex.quote(path.name))
        elif arm_elf(path):
            saved = path.with_name(path.name+'.native')
            path.rename(saved)
            wrapper(path, '/bin/'+shlex.quote(saved.name))
    for name in ('flock', 'dirname', 'readlink', 'sed', 'stat', 'tr', 'head', 'tail'):
        if not (system_bin/name).exists():
            wrapper(system_bin/name, '/tmp/nx300m-panel-runtime/interpreter/busybox '+name)
    for path in list((panel/'bin').iterdir()):
        if arm_elf(path):
            saved = path.with_name(path.name+'.native')
            path.rename(saved)
            wrapper(path, '/mod/bin/'+shlex.quote(saved.name))
            wrapped.append(path.name)
    (panel/'.state').mkdir(exist_ok=True)
    for path in [panel, *panel.rglob('*')]:
        os.chown(path, 65534, 65534, follow_symlinks=False)
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.suffix == '.sh' or path.parent.name == 'bin' else 0o644)
    (guest/'mod').mkdir(exist_ok=True)
    subprocess.run(['mount', '--bind', str(system_bin), str(guest/'bin')], check=True)
    subprocess.run(['mount', '--bind', str(panel), str(guest/'mod')], check=True)
    return {'root': '/mod', 'state_uid': 65534, 'camera_owner': owner,
            'private_panel_qemu_identity': True, 'wrapped_arm_programs': wrapped,
            'preserved_existing_mod': True, 'global_binfmt_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.guest, args.bundle), indent=2))


if __name__ == '__main__':
    main()
