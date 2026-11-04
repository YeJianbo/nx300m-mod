"""Open the cross-compiled Metol panel on the running stock camera's X server."""
from wsl_paths import wsl_path
import argparse
import base64
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import tempfile
from session_stock import inspect_session
from look_library import LookLibrary
from look_catalog import camera_looks
from lut_table_data import read_tables
from cube_to_d4 import DEFAULT_METHOD, METHODS

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'

def deploy_looks(pid, *, method=DEFAULT_METHOD):
    output = PROJECT / 'outputs/metol'
    output.mkdir(parents=True, exist_ok=True)
    # Snapshot sources before publishing so concurrent builds cannot change a
    # table between validation and copy. Each caller owns its staging files.
    with tempfile.TemporaryDirectory(prefix='looks-sync-', dir=output) as staging:
        return _deploy_looks(pid, Path(staging), method=method)

def _deploy_looks(pid, staging, *, method=DEFAULT_METHOD):
    output = PROJECT / 'outputs/metol'
    library = LookLibrary()
    # Opening/selecting uses published tables, checked below as one snapshot.
    # Offline fitting belongs to explicit import/sync, not a UI action: even a
    # compiler revision change must not re-fit the whole catalogue under the
    # viewer's session lock when the user opens a menu.
    looks = camera_looks(library)
    if method is not None:
        looks = [library.ensure_compiled(m['id'], method=method) for m in looks]
    if len(looks) > 256: raise ValueError('Metol 当前面板最多显示 256 项 LUT')
    for look in looks:
        library.directory(look['id'])
        library.label(look['label'])
        if any(ord(c) < 32 or ord(c) == 127 for c in look['label']):
            raise ValueError('LUT 名称不能包含控制字符')
        tables = read_tables(library.directory(look['id']), look)
        target = staging / look['id']
        target.mkdir()
        for name, data in tables.items():
            (target / name).write_bytes(data)
    (staging/'looks.txt').write_text(''.join('|'.join([look['id'], look['label'], *['0']*7])+'\n' for look in looks), encoding='utf-8')
    (staging/'lut_engine.arm').write_bytes((output/'lut_engine.arm').read_bytes())
    payload = {'files': {}, 'remove': []}
    for path in staging.rglob('*'):
        if path.is_file():
            name = path.relative_to(staging).as_posix()
            if '/' in name:
                name = 'looks/'+name
            payload['files'][name] = base64.b64encode(path.read_bytes()).decode('ascii')
    for look in looks:
        if look.get('native_stock_3d_table') != 'table-stock3d.d4lut':
            payload['remove'].append('looks/'+look['id']+'/table-stock3d.d4lut')
    helper = wsl_path(PROJECT/'tools/deploy_simulator_assets.py')
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--mount',
                             'python3', helper, GUEST], input=json.dumps(payload).encode('utf-8'),
                            check=True, capture_output=True, timeout=20)
    (output/'catalog-deploy.json').write_bytes(result.stdout)
    return looks

def sync_looks(*, method=DEFAULT_METHOD):
    """Publish the local catalog without opening a panel or changing selection."""
    state = inspect_session(refresh=True)
    if not state['running']: raise RuntimeError('原厂程序尚未运行')
    looks = deploy_looks(state['namespace_pid'], method=method)
    return {'status': 'synced', 'count': len(looks),
            'ids': [look['id'] for look in looks],
            'selection_changed': False, 'reload_required_for_updated_selection': True,
            'message': '目录已同步；若更新了当前选中的 LUT，请重新选择，等待原厂重载。'}

def engine_action(action, key=None, *, method=None):
    state = inspect_session()
    if not state['running']: raise RuntimeError('原厂程序尚未运行')
    if key is not None: LookLibrary().get(key)
    deploy_looks(state['namespace_pid'], method=method)
    command = ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount', '--net', '--ipc', 'chroot', '--userspec=65534:65534', GUEST, '/qemu-arm-static', '-cpu', 'cortex-a9', '-L', '/', '/mod/lut_engine.arm', action]
    if key is not None: command.append(key)
    result = subprocess.run(command, capture_output=True, timeout=15)
    if result.returncode:
        detail = (result.stdout+result.stderr).decode('utf-8', 'replace').strip()
        raise RuntimeError(f'LUT engine {action!r} exited {result.returncode}: {detail or "no diagnostic output"}')
    return result.stdout.decode('utf-8', 'replace').strip()

def open_panel(mode='recipes', *, method=None):
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
    base = wsl_path(PROJECT)
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
        if not camera_looks(LookLibrary()): raise RuntimeError('LUT 目录为空，请先启用风格')
        engine_action('begin', method=method)
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
    parser.add_argument('--method', choices=METHODS, default=None,
                        help='LUT compiler; omitted to retain the current compiled catalog')
    args = parser.parse_args()
    open_panel('lut' if args.lut else 'settings' if args.settings else 'recipes', method=args.method)
