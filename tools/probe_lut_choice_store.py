"""Directed regression for the dual-slot LUT choice store and cache eviction
(2026-11-11 idle round).

Real ARM engine (fixture identity gate) inside an isolated mount/PID namespace
with its own tmpfs /var/run. Covers:
  - legacy lut-choice migration and continued readability
  - dual-slot writes, interrupted-slot fallback, corrupt-slot fallback
  - corrupt-everywhere never reads as applied or Off
  - deliberate Off persists and survives a simulated restart
  - empty/truncated legacy is NOT applied (the old first-byte bug)
  - strength consistency through save/restore
  - eviction of 100 stale caches to the bound, symlink non-following,
    unknown-file preservation, failed-deletion honesty and reference
    protection, with the boot path still restoring afterwards
No camera, no SD card, no hardware claim.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/2026-11-11-idle/choice-store-vm'
FIXTURE = '''#include "mod/metol/lut_engine_shared.h"
#define nx_lut_hook_ready() (getenv("FIXTURE_HOOK_READY")?1:0)
#define main nx_fixture_engine_main
#include "mod/metol/lut_engine.c"
#undef main
int main(int argc,char **argv) {
    if(argc==2&&!strcmp(argv[1],"fixture-evict")) {
        int lock=nx_lut_lock(0,1);if(lock<0)return 4;
        int left=nx_tune_cache_evict(nx_lut_data_root(),"");close(lock);
        return left<0?4:0;
    }
    return nx_fixture_engine_main(argc,argv);
}
'''
PREVIEW_CHECK = '''#include "mod/metol/lut_engine_shared.h"
#include "mod/metol/nxui_preview_state.h"
int main(void) {
    const char *root=getenv("NX300M_MOD_ROOT");
    printf("APPLIED=%d\\n",nx_preview_lut_applied(root));
    printf("STATE=%d\\n",nx_preview_state(root));
    return 0;
}
'''


def worker(out):
    subprocess.run(['mount', '-t', 'tmpfs', 'lut-choice-test', '/var/run'], check=True)
    data = out/'data'
    data.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, NX300M_MOD_ROOT=str(data))
    for stale in ('NX300M_MOD_SOURCE_ROOT', 'NX300M_MOD_SOURCE_ID', 'NX_STATE_DIR',
                  'NX300M_MFW_RAM_BASE', 'FIXTURE_HOOK_READY'):
        env.pop(stale, None)
    source = ROOT/'looks'
    key = 'nx300-hard-mono'
    target = data/'looks'/key
    target.mkdir(parents=True, exist_ok=True)
    for name in ('table.d4lut', 'table-stock3d.d4lut'):
        if (source/key/name).is_file():
            shutil.copyfile(source/key/name, target/name)
    (data/'looks.txt').write_text(key+'|Mono|0|0|0|0|0|0|0\n')
    commands = []

    def boot_debug(label):
        st = status()
        print(f'BOOT-FAIL {label}: status={st}', flush=True)
        print('state files:', sorted(x.name for x in (data/'.state').iterdir()), flush=True)
        for slot in slots():
            print(' slot', slot.name, repr(slot.read_text()[:90]), flush=True)

    def run(*args, expect=0, hook=True):
        local = dict(env)
        if hook:
            local['FIXTURE_HOOK_READY'] = '1'
        else:
            local.pop('FIXTURE_HOOK_READY', None)
        r = subprocess.run([str(ROOT/'work/qemu/qemu-arm-static'), '-L', '/opt/nx300m-lab/stock-1.15',
                            str(out/'engine.arm'), *map(str, args)], env=local,
                           capture_output=True, text=True)
        commands.append(dict(args=[str(a) for a in args], exit_code=r.returncode,
                             stdout=r.stdout[-240:], stderr=r.stderr[-400:]))
        if r.returncode != expect:
            raise RuntimeError(f'{args}: rc={r.returncode} (want {expect}) OUT={r.stdout!r} ERR={r.stderr!r}')
        if expect == 0 and 'PERSIST-WARNING' in r.stderr:
            raise RuntimeError(f'{args}: success exit with persistence failure: {r.stderr}')
        return r

    def preview(expect=None):
        r = subprocess.run([str(ROOT/'work/qemu/qemu-arm-static'), '-L', '/opt/nx300m-lab/stock-1.15',
                            str(out/'preview_check.arm')], env=env, capture_output=True, text=True)
        commands.append(dict(args=['preview_check'], exit_code=r.returncode,
                             stdout=r.stdout[-160:], stderr=r.stderr[-200:]))
        if r.returncode != 0:
            raise RuntimeError('preview_check: '+r.stdout+r.stderr)
        applied = int(r.stdout.split('APPLIED=')[1].split()[0])
        if expect is not None and applied != expect:
            raise RuntimeError(f'APPLIED={applied}, want {expect}')
        return applied

    def status():
        line = [l for l in run('choice-status').stdout.splitlines() if l.startswith('CHOICE ')][0]
        tokens = line.split()[1:]
        fields = dict(part.split('=', 1) for part in tokens if '=' in part)
        fields['source'] = 'slot' if 'slot' in tokens else ('legacy' if 'legacy' in tokens else 'none')
        return fields

    def clear_ram():
        for name in ('nx300m-lut-selection', 'nx300m-lut-selection-session',
                     'nx300m-lut-generation', 'nx300m-preview-backend', 'nx300m-lut-ram-hook'):
            p = Path('/var/run')/name
            if p.exists():
                p.unlink()

    def selected():
        return Path('/var/run/nx300m-lut-selection').read_text().split()

    def slots():
        return sorted((data/'.state').glob('lut-choice.[01]'))

    report = dict(passed=False, hardware_pixels_verified=False, camera_ready_is_fixture=True,
                  scope='Real ARM engine fixture, isolated mount/PID namespace')
    state = data/'.state'
    table = f'{data}/looks/{key}/table.d4lut'

    # --- 1. legacy migration -------------------------------------------------
    state.mkdir(parents=True, exist_ok=True)
    (state/'lut-choice').write_text(f'7 {table} 80\n')
    clear_ram()
    fields = status()
    report['legacy_read'] = fields.get('valid') == '1' and fields.get('strength') == '80' and fields.get('source') == 'legacy'
    try:
        run('boot')
    except RuntimeError:
        import traceback
        print('BOOT FAILED; status:', status())
        print('root listing:', sorted(str(x) for x in data.rglob('*'))[:12])
        print('looks listing:', sorted(str(x) for x in (data/'looks').iterdir()))
        raise
    report['legacy_boot_restores'] = selected()[2] == '80' and selected()[1] == table
    run('apply', key, '100', '3', '0', '0', '0', '0', '0', '0')
    report['slots_written'] = len(slots()) >= 1
    report['legacy_removed_after_write'] = not (state/'lut-choice').exists()
    fields = status()
    report['slot_read'] = fields.get('valid') == '1' and fields.get('source') == 'slot'
    tuned_dir = Path(selected()[1]).parent
    clear_ram()
    try:
        run('boot')
    except RuntimeError:
        boot_debug('slot-boot')
        raise
    report['slot_boot_restores'] = Path(selected()[1]).parent == tuned_dir

    # --- 2. interrupted newer slot ------------------------------------------
    run('apply', key, '100', '5', '0', '0', '0', '0', '0', '0')
    newer = Path(selected()[1]).parent
    ordered = sorted(slots(), key=lambda p: int(p.read_text().split()[1]))
    newest_slot = ordered[-1]
    older_slot = ordered[0]
    older_text = older_slot.read_bytes()
    body = newest_slot.read_bytes()
    newest_slot.write_bytes(body[:len(body)//2])          # simulated short write
    fields = status()
    report['interrupted_slot_falls_back'] = (
        fields.get('valid') == '1' and fields.get('source') == 'slot'
        and fields.get('generation') != '0')
    clear_ram()
    run('boot')
    report['interrupted_slot_restores_previous'] = Path(selected()[1]).parent != newer
    # the older slot content must still be intact after the failed sibling
    report['older_slot_untouched'] = older_slot.read_bytes() == older_text
    # repair by writing again: engine rewrites the damaged (older-gen) slot
    run('apply', key, '100', '6', '0', '0', '0', '0', '0', '0')
    report['recovery_write_succeeds'] = status().get('valid') == '1'

    # --- 3. both slots corrupt, legacy valid --------------------------------
    for slot in slots():
        slot.write_text('garbage without any newline')
    (state/'lut-choice').write_text(f'9 {table} 55\n')
    fields = status()
    report['legacy_fallback_after_slot_damage'] = fields.get('valid') == '1' and fields.get('strength') == '55'
    clear_ram()
    run('boot')
    report['legacy_fallback_restores_55'] = selected()[2] == '55' and selected()[1] == table

    # --- 4. everything corrupt ----------------------------------------------
    for slot in slots():
        slot.write_text('NXCH1 1 00000000 0\n')            # checksum mismatch
    (state/'lut-choice').write_text('')
    fields = status()
    report['all_corrupt_invalid'] = fields.get('valid') == '0' and fields.get('recorded') == '1'
    clear_ram()
    run('boot', expect=5)
    report['all_corrupt_boot_refuses'] = not Path('/var/run/nx300m-lut-selection').exists()
    report['empty_file_not_applied'] = preview(0) == 0

    # --- 5. deliberate Off --------------------------------------------------
    run('stock')
    fields = status()
    report['off_persisted'] = fields.get('valid') == '1' and fields.get('off') == '1'
    clear_ram()
    run('boot', hook=False)
    report['off_boot_stays_off'] = not Path('/var/run/nx300m-lut-selection').exists()
    report['off_not_applied'] = preview(0) == 0
    report['off_still_recorded'] = status().get('off') == '1'

    # --- 6. strength consistency through a fresh restart --------------------
    run('apply', key, '37', '2', '0', '0', '0', '0', '0', '0')
    strength_dir = Path(selected()[1]).parent
    clear_ram()
    run('boot')
    report['strength_restored_37'] = selected()[2] == '37' and Path(selected()[1]).parent == strength_dir

    # --- 7. eviction: 100 stale + anomalies ---------------------------------
    looks = data/'looks'
    old = time.time()-300
    for i in range(100):
        d = looks/f'nx-tune-{i:016x}'
        d.mkdir(exist_ok=True)
        (d/'tune.txt').write_text('fixture\n')
        (d/'table.d4lut').write_bytes(b'\0'*4380)
        (d/'table-stock3d.d4lut').write_bytes(b'\0'*4380)
        os.utime(d, (old+i, old+i))
    outside = data/'outside-cache'
    outside.mkdir(exist_ok=True)
    sentinel = outside/'keep.txt'
    sentinel.write_text('must survive')
    live = looks/'nx-tune-00000000000000e0'
    if live.is_symlink() or live.exists():
        live.unlink()
    live.symlink_to(outside, target_is_directory=True)
    dangling = looks/'nx-tune-00000000000000e1'
    if dangling.is_symlink() or dangling.exists():
        dangling.unlink()
    dangling.symlink_to(looks/'definitely-missing', target_is_directory=True)
    unknown = looks/'nx-tune-00000000000000e2'
    unknown.mkdir(exist_ok=True)
    (unknown/'tune.txt').write_text('fixture\n')
    (unknown/'stranger.dat').write_bytes(b'user data')
    os.utime(unknown, (old, old))
    # A deletion that cannot complete: an owned name occupied by a directory.
    # unlink() fails with EISDIR, so the victim must stay and be reported as
    # failed, never counted as collected. (chmod cannot block root.)
    undeletable = looks/'nx-tune-00000000000000e3'
    undeletable.mkdir(exist_ok=True)
    (undeletable/'tune.txt').write_bytes(b''*16)
    (undeletable/'table.d4lut').mkdir()
    ((undeletable/'table.d4lut')/'nested.bin').write_bytes(b'x')
    os.utime(undeletable, (old, old))
    ram_dir = Path(selected()[1]).parent
    choice_dir = Path(status()['table']).parent
    before_dirs = [p for p in looks.iterdir() if p.is_dir() and not p.is_symlink() and p.name.startswith('nx-tune-')]
    r = run('apply', key, '37', '4', '0', '0', '0', '0', '0', '0')
    evict_line = [l for l in r.stderr.splitlines() if l.startswith('TUNE_CACHE_EVICT')]
    report['evict_reported'] = bool(evict_line)
    if evict_line:
        report['evict_line'] = evict_line[0]
        counts = dict(part.split('=') for part in evict_line[0].split()[1:])
        report['evict_failed_reported'] = counts.get('failed') == '1'
        report['evict_anomalous_reported'] = counts.get('anomalous') == '1'
        report['evict_links_reported'] = counts.get('links') == '2'
        report['evict_reached_target'] = counts.get('after') == '24'
    after_dirs = [p for p in looks.iterdir() if p.is_dir() and not p.is_symlink() and p.name.startswith('nx-tune-')]
    report['evict_bound_100_to_24'] = len(after_dirs) == 24
    report['outside_file_survives'] = sentinel.exists()
    report['live_symlink_survives'] = live.is_symlink()
    report['dangling_symlink_survives'] = dangling.is_symlink()
    report['unknown_file_dir_survives'] = (unknown/'stranger.dat').exists() and (unknown/'tune.txt').exists()
    report['undeletable_dir_survives'] = (undeletable/'table.d4lut'/'nested.bin').exists()
    report['ram_selection_dir_survives'] = ram_dir.exists()
    report['choice_dir_survives'] = choice_dir.exists()
    clear_ram()
    run('boot')
    report['boot_restores_after_eviction'] = Path(selected()[1]).parent == Path(status()['table']).parent

    # A valid older slot is a recovery reference even while a newer slot wins.
    run('apply', key, '37', '6', '0', '0', '0', '0', '0', '0')
    ordered = sorted(slots(), key=lambda p: int(p.read_text().split()[1]))
    fallback_table = Path(ordered[0].read_text().split()[4])
    report['recovery_slots_reference_distinct_tables'] = fallback_table != Path(ordered[-1].read_text().split()[4])
    for i in range(30):
        d = looks/f'nx-tune-{0x1000+i:016x}'
        d.mkdir()
        (d/'tune.txt').write_text('fixture\n')
        (d/'table.d4lut').write_bytes(bytes(4380))
        os.utime(d, (old, old))
    anomaly = looks/'nx-tune-user-not-generated'
    anomaly.mkdir()
    (anomaly/'tune.txt').write_text('user data\n')
    (anomaly/'table.d4lut').write_bytes(b'user table')
    os.utime(anomaly, (1, 1))
    os.utime(fallback_table.parent, (1, 1))
    run('fixture-evict')
    report['older_slot_table_survives_eviction'] = fallback_table.exists()
    report['invalid_generator_name_preserved'] = (anomaly/'tune.txt').read_text() == 'user data\n'
    ordered[-1].write_text('damaged\n')
    clear_ram()
    run('boot')
    report['older_slot_boot_after_eviction_and_newest_damage'] = selected()[1] == str(fallback_table)

    report['passed'] = all(value is True for name, value in report.items()
                           if isinstance(value, bool)
                           and name not in ('hardware_pixels_verified', 'passed'))
    report['commands'] = commands
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
    (OUT/'preview_check.c').write_text(PREVIEW_CHECK)
    cmd = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
COMMON="-std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined -Wl,-rpath-link,\\"$rf/lib\\""
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" $COMMON \\
 {shlex.quote(wsl_path(OUT/'engine.c'))} mod/metol/remote/compat.c \\
 work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc \\
 -o {shlex.quote(wsl_path(OUT/'engine.arm'))}
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" $COMMON -Wno-unused-function \\
 {shlex.quote(wgl(OUT/'preview_check.c'))} mod/metol/remote/compat.c \\
 work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc \\
 -o {shlex.quote(wsl_path(OUT/'preview_check.arm'))}
exec unshare --mount --net --ipc --pid --fork --mount-proc python3 {shlex.quote(wsl_path(__file__))} --worker {shlex.quote(wsl_path(OUT))}
'''
    r = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', cmd], capture_output=True, text=True)
    (OUT/'build.log').write_text(r.stdout+r.stderr)
    if r.returncode:
        raise RuntimeError(f'ARM choice-store probe exit {r.returncode}: {r.stderr[-3000:]}')
    print((OUT/'report.json').read_text())


def wgl(path):
    from wsl_paths import wsl_path
    return wsl_path(path)


if __name__ == '__main__':
    main()
