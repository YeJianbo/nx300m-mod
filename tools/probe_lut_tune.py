"""Run production LUT staging/tuning actions as ARM code in an isolated mount.

Only the camera-ready identity gate is replaced. No camera process, firmware
callback, hardware interpolation or physical colour accuracy is measured.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/review/menu-toggle-width/tuning-arm'
FIXTURE = '''#include "mod/metol/lut_engine_shared.h"
#define nx_lut_hook_ready() 1
#include "mod/metol/lut_engine.c"
'''


def worker(out):
    import os
    subprocess.run(['mount', '-t', 'tmpfs', 'lut-tune-test', '/var/run'], check=True)
    data = out/'data'
    data.mkdir(exist_ok=True)
    env = dict(os.environ, NX300M_MOD_ROOT=str(data))
    for key in ('NX300M_MOD_SOURCE_ROOT', 'NX300M_MOD_SOURCE_ID', 'NX_STATE_DIR', 'NX300M_MFW_RAM_BASE'):
        env.pop(key, None)
    source = ROOT/'looks'
    keys = ('nx300-hard-mono', 'nx300-fuji-classic-chrome')
    for key in keys:
        target = data/'looks'/key
        target.mkdir(parents=True, exist_ok=True)
        for name in ('table.d4lut', 'table-stock3d.d4lut'):
            if (source/key/name).is_file():
                shutil.copyfile(source/key/name, target/name)
    (data/'looks.txt').write_text(''.join(k+'|'+k+'|0|0|0|0|0|0|0\n' for k in keys))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in data.glob('looks/*/*.d4lut')}
    commands = []

    def run(*args, expect=0):
        r = subprocess.run([str(ROOT/'work/qemu/qemu-arm-static'), '-L', '/opt/nx300m-lab/stock-1.15',
                            str(out/'engine.arm'), *map(str, args)], env=env, capture_output=True, text=True)
        commands.append(dict(args=args, exit_code=r.returncode, stdout=r.stdout, stderr=r.stderr))
        if r.returncode != expect:
            raise RuntimeError(str(args)+': '+r.stdout+r.stderr)
        return r

    def selected():
        return Path('/var/run/nx300m-lut-selection').read_text().split()

    run('apply', keys[0], 100, 0, 0, 0, 0, 0, 0, 0)
    assert Path(selected()[1]) == data/'looks'/keys[0]/'table.d4lut'
    run('apply', keys[0], 100, 2, -1, 0, 0, 0, 2, -1)
    mono = Path(selected()[1])
    words = struct.unpack('<2190H', mono.read_bytes())
    assert words[:729] == words[730:1459] == words[1460:2189]
    run('apply', keys[1], 75, 2, -1, 3, 1, -2, 2, -1)
    state = selected()
    table = Path(state[1])
    for p in table.parent.glob('*.d4lut'):
        values = struct.unpack('<2190H', p.read_bytes())
        assert max(values) <= 1023 and all(values[i] == 0 for i in (729,1459,2189))
        assert p.read_bytes() != (data/'looks'/keys[1]/p.name).read_bytes()
    stamp = table.stat().st_mtime_ns
    run('apply', keys[1], 75, 2, -1, 3, 1, -2, 2, -1)
    assert selected()[1:] == state[1:] and table.stat().st_mtime_ns == stamp
    for name in ('nx300m-lut-selection', 'nx300m-lut-selection-session'):
        (Path('/var/run')/name).unlink()
    run('restore-selection')
    assert selected()[1:] == state[1:]
    run('apply', keys[0], 100)
    assert Path(selected()[1]) == mono
    run('apply', keys[1], 75)
    assert Path(selected()[1]) == table
    # Boot explicitly replaces stale RAM choice with the card's saved choice.
    Path('/var/run/nx300m-lut-selection').write_text('99 '+str(mono)+' 100\n')
    run('boot')
    assert selected()[1:] == state[1:]
    run('save-preview', 1)
    assert (data/'.state/lut-preview').read_text() == '1\n'
    run('save-preview', 0)
    assert (data/'.state/lut-preview').read_text() == '0\n'
    prior = selected()
    payload = table.read_bytes()
    table.write_bytes(b'bad')
    run('apply', keys[1], 75, 2, -1, 3, 1, -2, 2, -1, expect=5)
    assert selected() == prior
    table.write_bytes(payload)
    run('apply', keys[1], 100, 0, 0, 0, 0, 0, 0, 0)
    assert Path(selected()[1]) == data/'looks'/keys[1]/'table.d4lut'
    assert before == {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before}
    # Exercise the current shell cycle with a favourite outside the short
    # cycle catalogue. Only the feedback panel is replaced by an exit-0 stub.
    (data/'bin').mkdir(exist_ok=True)
    qemu=ROOT/'work/qemu/qemu-arm-static'
    (data/'bin/lut_engine.arm').write_text('#!/bin/sh\nexec '+shlex.quote(str(qemu))+' -L /opt/nx300m-lab/stock-1.15 '+shlex.quote(str(out/'engine.arm'))+' "$@"\n')
    (data/'bin/lut_engine.arm').chmod(0o700)
    (data/'lut-panel.sh').write_text('#!/bin/sh\nexit 0\n')
    (data/'looks-cycle.txt').write_text(keys[0]+'|Mono|0|0|0|0|0|0|0\n')
    (data/'.state/lut-favorites').write_text('\n'.join(keys)+'\n')
    run('apply',keys[0],100)
    cycle=ROOT/'mod/deployment/mfw/lut-cycle.sh'
    r=subprocess.run(['/bin/sh',str(cycle)],env=dict(env,NX300M_MOD_SOURCE_ROOT=str(data)),capture_output=True,text=True)
    if r.returncode:raise RuntimeError('Tuned favourite cycle: '+r.stdout+r.stderr)
    assert Path(selected()[1]) == data/'looks'/keys[1]/'table.d4lut'
    report = dict(passed=True, hardware_pixels_verified=False, camera_ready_is_fixture=True,
                  identity_is_byte_exact=True, neutral_controls_preserve_mono=True,
                  both_native_table_branches_adjusted=True, unchanged_cache_not_rewritten=True,
                  selection_and_tuning_restored=True, per_look_preferences_recalled=True,
                  corrupt_cache_rejected_before_selection=True, defaults_restore_base_table=True,
                  original_tables_unchanged=True, commands=commands)
    report.update(boot_restores_card_over_stale_ram=True,preview_preference_persisted=True)
    report.update(tuned_favorite_outside_short_cycle_checked=True,lut_cycle_sha256=hashlib.sha256(cycle.read_bytes()).hexdigest())
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    from wsl_paths import wsl_path
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'engine.c').write_text(FIXTURE)
    cmd = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. \
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 -mfloat-abi=soft \
 -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \
 {shlex.quote(wsl_path(OUT/'engine.c'))} mod/metol/remote/compat.c \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc \
 -o {shlex.quote(wsl_path(OUT/'engine.arm'))}
exec unshare --mount --net --ipc --pid --fork --mount-proc python3 {shlex.quote(wsl_path(__file__))} --worker {shlex.quote(wsl_path(OUT))}
'''
    r = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',cmd], capture_output=True, text=True)
    (OUT/'build.log').write_text(r.stdout+r.stderr)
    if r.returncode:
        raise RuntimeError(f'ARM tune probe exit {r.returncode}: {r.stderr}')
    print((OUT/'report.json').read_text())


if __name__ == '__main__':
    main()
