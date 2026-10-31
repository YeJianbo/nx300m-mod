"""Open the cross-compiled Metol panel on the running stock camera's X server."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import time
import struct
import tempfile
from session_stock import inspect_session
from look_library import LookLibrary

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'

def deploy_looks(pid):
    output = PROJECT / 'outputs/metol'
    output.mkdir(parents=True, exist_ok=True)
    # Snapshot sources before publishing so concurrent builds cannot change a
    # table between validation and copy. Each caller owns its staging files.
    with tempfile.TemporaryDirectory(prefix='looks-sync-', dir=output) as staging:
        return _deploy_looks(pid, Path(staging))

def _deploy_looks(pid, staging):
    output = PROJECT / 'outputs/metol'
    library = LookLibrary()
    looks = library.list()
    if len(looks) > 256: raise ValueError('Metol 当前面板最多显示 256 项 LUT')
    for look in looks:
        library.directory(look['id'])
        library.label(look['label'])
        if any(ord(c) < 32 or ord(c) == 127 for c in look['label']):
            raise ValueError('LUT 名称不能包含控制字符')
        target = staging / look['id']
        target.mkdir()
        names = ['table.d4lut']
        if look.get('native_stock_3d_table') == 'table-stock3d.d4lut':
            names.append('table-stock3d.d4lut')
        for name in names:
            data = (library.directory(look['id']) / name).read_bytes()
            if len(data) != 4380:
                raise ValueError(f'{look["id"]}/{name}: D4 表必须为 4380 字节')
            values = struct.unpack('<2190H', data)
            if max(values) > 1023 or any(values[i] for i in (729, 1459, 2189)):
                raise ValueError(f'{look["id"]}/{name}: D4 数据或填充无效')
            (target / name).write_bytes(data)
    (staging/'looks.txt').write_text(''.join('|'.join([look['id'], look['label'], *['0']*7])+'\n' for look in looks), encoding='utf-8')
    (staging/'lut_engine.arm').write_bytes((output/'lut_engine.arm').read_bytes())
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    source = base + '/' + staging.relative_to(PROJECT).as_posix()
    lock = shlex.quote(GUEST+'/var/run/nx300m-lut-engine.lock')
    lines = ['set -eu',
             f'(umask 077; set -C; : > {lock}) 2>/dev/null && chown 65534:65534 {lock} || test -f {lock}',
             f'exec 9< {lock}', 'flock -x -w 10 9',
             f'mkdir -p {shlex.quote(GUEST+"/mod/looks")}']
    for look in looks:
        target = GUEST+'/mod/looks/'+look['id']
        lines += [f'mkdir -p {shlex.quote(target)}', f'cp {shlex.quote(source+"/"+look["id"]+"/table.d4lut")} {shlex.quote(target+"/table.d4lut.tmp")}', f'mv {shlex.quote(target+"/table.d4lut.tmp")} {shlex.quote(target+"/table.d4lut")}']
        if look.get('native_stock_3d_table') == 'table-stock3d.d4lut':
            lines += [f'cp {shlex.quote(source+"/"+look["id"]+"/table-stock3d.d4lut")} {shlex.quote(target+"/table-stock3d.d4lut.tmp")}', f'mv {shlex.quote(target+"/table-stock3d.d4lut.tmp")} {shlex.quote(target+"/table-stock3d.d4lut")}']
        else: lines.append(f'rm -f {shlex.quote(target+"/table-stock3d.d4lut")}')
    for name, target in [('lut_engine.arm', '/mod/lut_engine.arm'), ('looks.txt', '/mod/looks.txt')]:
        lines.append(f'cp {shlex.quote(source+"/"+name)} {shlex.quote(GUEST+target+".tmp")}')
        lines.append(f'mv {shlex.quote(GUEST+target+".tmp")} {shlex.quote(GUEST+target)}')
    lines.append(f'chmod 755 {shlex.quote(GUEST+"/mod/lut_engine.arm")}')
    subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--mount', 'sh', '-c', '\n'.join(lines)], check=True, capture_output=True, timeout=20)
    return looks

def sync_looks():
    """Publish the local catalog without opening a panel or changing selection."""
    state = inspect_session(refresh=True)
    if not state['running']: raise RuntimeError('原厂程序尚未运行')
    looks = deploy_looks(state['namespace_pid'])
    return {'status': 'synced', 'count': len(looks),
            'ids': [look['id'] for look in looks],
            'selection_changed': False, 'reload_required_for_updated_selection': True,
            'message': '目录已同步；若更新了当前选中的 LUT，请重新选择，等待原厂重载。'}

def engine_action(action, key=None):
    state = inspect_session()
    if not state['running']: raise RuntimeError('原厂程序尚未运行')
    if key is not None: LookLibrary().get(key)
    deploy_looks(state['namespace_pid'])
    command = ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount', '--net', '--ipc', 'chroot', '--userspec=65534:65534', GUEST, '/qemu-arm-static', '-cpu', 'cortex-a9', '-L', '/', '/mod/lut_engine.arm', action]
    if key is not None: command.append(key)
    result = subprocess.run(command, capture_output=True, timeout=15)
    if result.returncode:
        detail = (result.stdout+result.stderr).decode('utf-8', 'replace').strip()
        raise RuntimeError(f'LUT engine {action!r} exited {result.returncode}: {detail or "no diagnostic output"}')
    return result.stdout.decode('utf-8', 'replace').strip()

def open_panel(mode='recipes'):
    if mode not in ('recipes', 'lut', 'settings'): raise ValueError('Unknown panel mode')
    state = inspect_session()
    if not state['running']: raise RuntimeError('原厂程序尚未运行')
    if state['metol_pids']:
        subprocess.run([sys.executable, str(PROJECT/'tools/input_stock.py'), 'key', 'Menu'], check=True, capture_output=True, timeout=15)
        deadline = time.monotonic()+15
        while inspect_session()['metol_pids']:
            if time.monotonic() > deadline: raise TimeoutError('Metol 正在结束当前操作，请稍后重试')
            time.sleep(.2)
    output = PROJECT / 'outputs/metol'
    if not (output / 'nxui.arm').exists(): raise RuntimeError('请先运行 tools/build_metol.py')
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    options = '--dry-run --root /mod --recipes /mod/recipes.txt'
    recipe_deploy = ''
    if mode in ('recipes', 'settings'):
        if not (output / 'recipe_engine.arm').exists(): raise RuntimeError('请先运行 tools/build_metol.py 构建配方引擎')
        recipe_deploy = f'''cp {shlex.quote(base + '/outputs/metol/recipe_engine.arm')} {shlex.quote(GUEST + '/mod/recipe_engine.arm.tmp')}
mv {shlex.quote(GUEST + '/mod/recipe_engine.arm.tmp')} {shlex.quote(GUEST + '/mod/recipe_engine.arm')}
chmod 755 {shlex.quote(GUEST + '/mod/recipe_engine.arm')}
'''
        if state.get('recipe_style_ready'):
            options = '--live --root /mod --recipes /mod/recipes.txt --engine /mod/recipe_engine.arm --arm-runner /qemu-arm-static'
        else:
            print('七维配方桥尚未就绪：面板保持浏览模式，当前会话不可应用。', file=sys.stderr)
        if mode == 'settings': options += ' --menu gui_ini.NX300M'
    if mode == 'lut':
        if not state.get('lut_hook_ready'): raise RuntimeError('请重启原厂程序以载入 LUT 适配器')
        if not LookLibrary().list(): raise RuntimeError('LUT 库为空，请先添加 .cube')
        engine_action('begin')
        options = '--live --lut --root /mod --recipes /mod/looks.txt --engine /mod/lut_engine.arm --arm-runner /qemu-arm-static'
    script = f'''set -eu
mkdir -p {shlex.quote(GUEST + '/mod')}
cp {shlex.quote(base + '/outputs/metol/nxui.arm')} {shlex.quote(GUEST + '/mod/nxui.arm')}
cp {shlex.quote(base + '/outputs/metol/recipes.txt')} {shlex.quote(GUEST + '/mod/recipes.txt')}
{recipe_deploy.rstrip()}
cp {shlex.quote(base + '/mod/deployment/gui_ini.NX300M')} {shlex.quote(GUEST + '/mod/gui_ini.NX300M')}
chmod 755 {shlex.quote(GUEST + '/mod/nxui.arm')}
exec chroot --userspec=65534:65534 {shlex.quote(GUEST)} /qemu-arm-static -cpu cortex-a9 -L / /mod/nxui.arm {options}
'''
    with (output / 'nxui.log').open('ab') as log:
        subprocess.Popen(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount', '--net', '--ipc', 'env', 'DISPLAY=:99', 'sh', '-c', script], stdout=log, stderr=subprocess.STDOUT)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--lut', action='store_true')
    modes.add_argument('--settings', action='store_true')
    args = parser.parse_args()
    open_panel('lut' if args.lut else 'settings' if args.settings else 'recipes')
