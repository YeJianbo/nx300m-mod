"""Run only persistent launch dispatch with stock ARM BusyBox and an honest host recorder."""
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

from build_stock_app_loader import PERSISTENT_HOOKS, LAUNCH_PROFILES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/app-loader/dispatch-probe'
CANDIDATE = ROOT / 'outputs/deployment/app-loader/boot-chain/persistent-candidate/rootfs'
ARGUMENTS = ['two words', '', 'star*literal', '中文', '-n', '$(literal)']
RECORDER = r'''#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
static void value(const char *s) {
    if (!s) { fputs("null", stdout); return; }
    putchar('"');
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { putchar('\\'); putchar(c); }
        else if (c < 32) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}
int main(int argc, char **argv) {
    char executable[1024];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof executable - 1);
    if (length < 0) return 38;
    executable[length] = 0;
    fputs("{\"argv\":[", stdout);
    for (int i = 0; i < argc; ++i) { if (i) putchar(','); value(argv[i]); }
    fputs("],\"NX300M_MOD_ROOT\":", stdout); value(getenv("NX300M_MOD_ROOT"));
    fputs(",\"LD_PRELOAD\":", stdout); value(getenv("LD_PRELOAD"));
    fputs(",\"executable\":", stdout); value(executable);
    fputs("}\n", stdout);
    return 37;
}
'''


def wsl(path):
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stage = OUT / 'stock'
    provenance = []
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        for source, destination in (
            ('bin/busybox', 'bin/arm/busybox'),
            ('lib/ld-2.13.so', 'lib/ld-linux.so.3'),
            ('lib/libc-2.13.so', 'lib/libc.so.6'),
            ('lib/libm-2.13.so', 'lib/libm.so.6'),
        ):
            data = archive.extractfile(source).read()
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            provenance.append({'stock_member': source, 'destination': destination, 'bytes': len(data)})
    (OUT / 'stock-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    (OUT / 'dispatch-recorder.c').write_text(RECORDER, encoding='utf-8')
    compile_script = f'cc -static -O2 -Wall -Wextra -Werror {shlex.quote(wsl(OUT / "dispatch-recorder.c"))} -o {shlex.quote(wsl(OUT / "dispatch-recorder"))}\n'
    compiled = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-s'],
                              input=compile_script.encode(), capture_output=True)
    (OUT / 'compile.stdout.log').write_bytes(compiled.stdout)
    (OUT / 'compile.stderr.log').write_bytes(compiled.stderr)
    if compiled.returncode:
        raise RuntimeError(f'Host recorder compile exited {compiled.returncode}: {compiled.stderr.decode("utf-8", "replace")}')

    app_call = (ROOT / 'mod/deployment/app-loader/hib2-app-call.sh').read_bytes()
    candidate_hib = (CANDIDATE / 'usr/bin/hib2.sh').read_bytes()
    if candidate_hib.count(app_call) != 1:
        raise ValueError('The exact dispatch fragment must occur once in the candidate hib2')
    # Only this extracted fragment is executed. No other hib2 command is copied.
    hib_dispatch = (b'app=di-camera-app-nx300\n' + app_call +
                    b'probe_child=$!\nwait "$probe_child"\nprobe_status=$?\n'
                    b'printf \'{"parent_root":"%s","parent_preload":"%s","child_exit":%s}\\n\' "$NX300M_MOD_ROOT" "$LD_PRELOAD" "$probe_status"\n'
                    b'exit "$probe_status"\n')
    (OUT / 'hib2-dispatch-only.sh').write_bytes(hib_dispatch)
    root = OUT / 'probe-root'
    root.mkdir(exist_ok=True)
    # Full-profile missing-library coverage stays relevant; also exercise the
    # default, each bounded group, override precedence and both disable paths.
    cases = [('desktop-all-present', 'desktop', None, False, 'full', None),
             ('desktop-missing-helper', 'desktop', None, True, 'full', None)]
    cases += [('desktop-missing-' + name, 'desktop', name, False, 'full', None) for name in PERSISTENT_HOOKS]
    cases += [('hib2-all-present', 'hib2', None, False, 'full', None),
              ('hib2-missing-helper', 'hib2', None, True, 'full', None),
              ('hib2-missing-service-loader.so', 'hib2', 'service-loader.so', False, 'full', None)]
    cases += [(entry + '-' + profile, entry, None, False, profile, None)
              for entry in ('desktop','hib2') for profile in LAUNCH_PROFILES if profile != 'full']
    cases += [('default-stock', 'desktop', None, False, None, None),
              ('missing-profile', 'desktop', None, False, None, 'missing-profile'),
              ('invalid-profile', 'desktop', None, False, '$(touch /injected)', None),
              ('empty-profile', 'desktop', None, False, '', None),
              ('sd-stock-overrides-full', 'desktop', None, False, 'stock', 'builtin-full'),
              ('selected-only', 'desktop', 'lut_port.so', False, 'services', None),
              ('sd-disabled', 'desktop', None, False, 'full', 'sd-disabled'),
              ('builtin-disabled', 'hib2', None, False, 'full', 'builtin-disabled')]
    results = []
    for label, entry, missing, missing_helper, profile, control in cases:
        script = f'''set -eu
mount --make-rprivate /
root={shlex.quote(wsl(root))}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(wsl(stage))}/. "$root"/
mkdir -p "$root/usr/bin" "$root/usr/lib/nx300m-mod" "$root/dev" "$root/proc" "$root/mnt/mmc/nx300m-mod"
cp {shlex.quote(wsl(ROOT / 'work/qemu/qemu-arm-static'))} "$root/qemu-arm-static"
cp {shlex.quote(wsl(OUT / 'dispatch-recorder'))} "$root/dispatch-recorder"
ln -s /dispatch-recorder "$root/usr/bin/di-camera-app"
ln -s /dispatch-recorder "$root/usr/bin/di-camera-app-nx300"
cp {shlex.quote(wsl(CANDIDATE / 'usr/bin/nx300m-camera-launch'))} "$root/usr/bin/nx300m-camera-launch"
cp {shlex.quote(wsl(CANDIDATE / 'usr/bin/hib2.sh'))} "$root/hib2-syntax-only.sh"
cp {shlex.quote(wsl(OUT / 'hib2-dispatch-only.sh'))} "$root/hib2-dispatch-only.sh"
'''
        if control != 'missing-profile':
            builtin = 'full\n' if control == 'builtin-full' else (CANDIDATE / 'usr/lib/nx300m-mod/launch-profile').read_text()
            script += 'printf %s ' + shlex.quote(builtin) + ' > "$root/usr/lib/nx300m-mod/launch-profile"\n'
        if profile is not None:
            script += 'printf %s ' + shlex.quote(profile) + ' > "$root/mnt/mmc/nx300m-mod/launch-profile"\n'
        if control in ('sd-disabled','builtin-disabled'):
            flag = 'mnt/mmc/nx300m-mod' if control == 'sd-disabled' else 'usr/lib/nx300m-mod'
            script += f'touch "$root/{flag}/DISABLED"\n'
        if not missing_helper:
            script += f'cp {shlex.quote(wsl(CANDIDATE / "usr/lib/nx300m-mod/preload.sh"))} "$root/usr/lib/nx300m-mod/preload.sh"\n'
        for name in PERSISTENT_HOOKS:
            if name != missing:
                script += f'cp {shlex.quote(wsl(CANDIDATE / "usr/lib/nx300m-mod" / name))} "$root/usr/lib/nx300m-mod/{name}"\n'
        script += '''chmod -R a+rX "$root"
chmod 755 "$root/qemu-arm-static" "$root/dispatch-recorder" "$root/bin/arm/busybox" "$root/usr/bin/nx300m-camera-launch"
mount -t proc proc "$root/proc"
touch "$root/dev/null"
mount --bind /dev/null "$root/dev/null"
'''
        command = ['chroot', '--userspec=65534:65534', str(root), '/qemu-arm-static', '-cpu', 'cortex-a9', '-L', '/', '/bin/arm/busybox', 'sh']
        command[2] = wsl(root)
        if label == 'desktop-all-present':
            for source in ('/usr/lib/nx300m-mod/preload.sh', '/usr/bin/nx300m-camera-launch', '/hib2-syntax-only.sh'):
                script += ' '.join(shlex.quote(x) for x in command + ['-n', source]) + '\n'
        invocation = 'NX300M_MOD_ROOT=/prior-root; LD_PRELOAD=/prior.so; export NX300M_MOD_ROOT LD_PRELOAD; '
        invocation += '. /usr/bin/nx300m-camera-launch "$@"' if entry == 'desktop' else '. /hib2-dispatch-only.sh'
        script += 'exec ' + ' '.join(shlex.quote(x) for x in command + ['-c', invocation, 'dispatch-shell'] + (ARGUMENTS if entry == 'desktop' else [])) + '\n'
        (OUT / (label + '-host.sh')).write_text(script, encoding='utf-8', newline='\n')
        process = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'unshare',
                                  '--mount', '--net', '--ipc', '--pid', '--fork', '--mount-proc', 'sh', '-s'],
                                 input=script.encode(), capture_output=True, timeout=25)
        (OUT / (label + '.stdout.log')).write_bytes(process.stdout)
        (OUT / (label + '.stderr.log')).write_bytes(process.stderr)
        observed = [json.loads(line) for line in process.stdout.decode('utf-8', 'replace').splitlines() if line.startswith('{')]
        selected = LAUNCH_PROFILES.get(profile, [])
        loaded = bool(selected) and missing not in selected and not missing_helper and control not in ('sd-disabled','builtin-disabled')
        preload = ':'.join('/usr/lib/nx300m-mod/' + name for name in selected) + ':/prior.so'
        expected_argv = ['/usr/bin/di-camera-app'] + ARGUMENTS if entry == 'desktop' else ['di-camera-app-nx300']
        passed = bool(observed) and process.returncode == 37 and not process.stderr
        if observed:
            passed = passed and observed[0] == {'argv': expected_argv,
                       'NX300M_MOD_ROOT': '/mnt/mmc/nx300m-mod' if loaded else '/prior-root',
                       'LD_PRELOAD': preload if loaded else '/prior.so', 'executable': '/dispatch-recorder'}
        if entry == 'hib2':
            passed = passed and len(observed) == 2 and observed[1] == {
                'parent_root': '/prior-root', 'parent_preload': '/prior.so', 'child_exit': 37}
        results.append({'case': label, 'entry': entry, 'missing_hook': missing, 'missing_helper': missing_helper,
                        'requested_profile': profile, 'control': control,
                        'exit_code': process.returncode, 'stderr': process.stderr.decode('utf-8', 'replace'),
                        'observed': observed, 'passed': passed})
    report = {'scope': 'exact wrapper/shared helper and extracted hib2 dispatch only; stock ARM BusyBox; static host recorder returns37',
              'stock_busybox_syntax_checked': True, 'whole_hib2_executed': False,
              'real_arm_hooks_loaded': False, 'camera_identity_spoofed': False,
              'fixture_target': 'private probe aliases resolve to /dispatch-recorder; /proc/self/exe verifies its honest host identity',
              'preload_scope': 'ARM SO bytes serve as presence inputs; static host target records LD_PRELOAD and does not load them',
              'isolation': 'fresh mount/net/IPC/PID namespace and private tmpfs root per case; uid65534, only /dev/null; no SD resources',
              'production_rootfs_changed': False, 'cases': results, 'passed': all(r['passed'] for r in results)}
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'cases': [{'case': c['case'], 'exit_code': c['exit_code'], 'passed': c['passed']} for c in results]}, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
