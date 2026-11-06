"""Windows SD-card updater for a checked NX300M deployment candidate."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import simpledialog
import uuid
import zipfile
from datetime import datetime
from installer_backend import verify_backend

BASE = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
MANIFEST = BASE / 'manifest.json'
RESTART = 'nx300m-mod/RESTART_SERVICE'
FIRST = BASE / 'first-install'


def prepare_first(card, identity):
    if identity['filesystem'].upper() not in ('FAT32', 'EXFAT'):
        raise ValueError('请选择 FAT32 或 exFAT 存储卡。')
    for name in ('autoexec.sh', 'nx300m-mod'):
        path = safe_path(card, name)
        if path.exists() or path.is_symlink():
            raise ValueError('卡上已有 ' + name + '，为保护现有配置，请使用升级流程或另一张卡。')
    info = json.loads((FIRST / 'manifest.json').read_text(encoding='utf-8'))
    if info.get('schema') != 1 or info.get('kind') != 'nx300m-first-install-local-candidate':
        raise ValueError('首次安装文件格式不受支持。')
    files = info['files']
    if 'autoexec.sh' not in files or 'nx300m-mod/mfw-service-entry.sh' not in files:
        raise ValueError('首次安装文件不完整。')
    size = 0
    for name, rec in files.items():
        if name != 'autoexec.sh' and not name.startswith('nx300m-mod/'):
            raise ValueError('安装包含不支持的路径：' + name)
        data = safe_path(FIRST / 'payload', name).read_bytes()
        if len(data) != rec['bytes'] or checksum(data) != rec['sha256']:
            raise ValueError('首次安装文件校验失败：' + name)
        size += len(data)
    verify_backend(lambda name: safe_path(FIRST / 'payload', name).read_bytes())
    if shutil.disk_usage(card).free < size + 4 * 1024 * 1024:
        raise ValueError('存储卡剩余空间不足。')
    return files


def install_first(card, identity):
    files = prepare_first(card, identity)
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化，请重新检查。')
    stage = card / ('.nx300m-install-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for name, rec in files.items():
            if volume(str(card)) != identity:
                raise OSError('写入期间存储卡已变化。')
            data = safe_path(FIRST / 'payload', name).read_bytes()
            if checksum(data) != rec['sha256']:
                raise ValueError('安装文件发生变化：' + name)
            target = safe_path(stage, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
        if volume(str(card)) != identity:
            raise OSError('写入期间存储卡已变化。')
        verify_backend(lambda name: safe_path(stage, name).read_bytes())
        for name in ('nx300m-mod', 'autoexec.sh'):
            target = safe_path(card, name)
            if target.exists() or target.is_symlink():
                raise OSError('目标已出现，已停止安装：' + name)
            os.rename(stage / name, target)
        stage.rmdir()
    except (OSError, ValueError) as exc:
        raise OSError(f'{exc}\n安装中断；请保留存储卡。临时目录：{stage}。如已出现 nx300m-mod，请先检查后再重试。') from exc


def uninstall_mod(card, identity):
    """Move the recognized boot entry and MOD into a reversible on-card backup."""
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化，请重新选择。')
    entry = safe_path(card, 'autoexec.sh')
    mod = safe_path(card, 'nx300m-mod')
    info = json.loads((FIRST / 'manifest.json').read_text(encoding='utf-8'))
    expected = info['files']['autoexec.sh']['sha256']
    if not entry.is_file() or checksum(entry.read_bytes()) != expected:
        raise ValueError('启动脚本与本工具不匹配，无法自动卸载；请保留现有脚本。')
    if not mod.is_dir() or not safe_path(card, 'nx300m-mod/mfw-service-entry.sh').is_file():
        raise ValueError('未找到可识别的 MOD 目录。')
    backup = safe_path(card, 'nx300m-mod-backup-' + uuid.uuid4().hex[:12])
    backup.mkdir()
    try:
        if volume(str(card)) != identity:
            raise OSError('存储卡信息已变化。')
        os.rename(entry, backup / 'autoexec.sh')
        if volume(str(card)) != identity:
            raise OSError('存储卡信息已变化。')
        os.rename(mod, backup / 'nx300m-mod')
    except OSError as exc:
        raise OSError(f'{exc}\n卸载未完成；请保留卡上文件，检查备份目录 {backup}。') from exc
    return backup


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('路径不安全：' + name)
    target = root.joinpath(*path.parts)
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink() or (item.exists() and getattr(item.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('发现链接或重解析点：' + name)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('路径超出目标目录：' + name)
    return target


def volume(root):
    root = str(root)
    if os.name != 'nt' or len(root) != 3 or root[1:] != ':\\':
        raise ValueError('请选择 Windows 可移动磁盘根目录。')
    kernel = ctypes.windll.kernel32
    if kernel.GetDriveTypeW(root) != 2:
        raise ValueError('请选择读卡器识别出的可移动磁盘。')
    label = ctypes.create_unicode_buffer(256)
    fs = ctypes.create_unicode_buffer(256)
    serial = ctypes.c_ulong()
    if not kernel.GetVolumeInformationW(root, label, 256, ctypes.byref(serial), None, None, fs, 256):
        raise OSError('无法读取磁盘信息，确认已插入存储卡。')
    return {'serial': f'{serial.value:08X}', 'label': label.value, 'filesystem': fs.value}


def prepare(card):
    info = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if info.get('kind') != 'nx300m-cold-boot-update' or info.get('schema') != 1:
        raise ValueError('升级包格式不受支持。')
    for name, expected in info['requires'].items():
        file = safe_path(card, name)
        if not file.is_file() or checksum(file.read_bytes()) != expected:
            raise ValueError('后端版本不匹配或文件缺失：' + name)
    writes = {}
    for name, rec in info['payload'].items():
        dest = safe_path(card, name)
        current = checksum(dest.read_bytes()) if dest.is_file() else None
        if dest.exists() and not dest.is_file():
            raise ValueError('目标不是普通文件：' + name)
        if current not in rec['accepted']:
            raise ValueError('目标版本不匹配：' + name)
        data = safe_path(BASE, rec['asset']).read_bytes()
        if checksum(data) != rec['sha256']:
            raise ValueError('升级包校验失败：' + name)
        writes[name] = data
    verify_backend(lambda name: writes[name] if name in writes else safe_path(card, name).read_bytes())
    return writes


def write_checked(path, data):
    fd, name = tempfile.mkstemp(prefix='.nx-update-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if temp.read_bytes() != data:
            raise OSError('临时文件回读失败：' + str(path))
        os.replace(temp, path)
        if path.read_bytes() != data:
            raise OSError('写入回读失败：' + str(path))
    finally:
        temp.unlink(missing_ok=True)


def apply(card, identity):
    writes = prepare(card)
    if volume(str(card))['serial'] != identity['serial']:
        raise ValueError('磁盘已更换，请重新检查。')
    backups = BASE / 'backups'
    if backups.resolve().is_relative_to(card.resolve()):
        raise ValueError('请先把程序完整解压到电脑。')
    backup = backups / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    records = {}
    restart = safe_path(card, RESTART)
    all_writes = dict(writes)
    all_writes[RESTART] = ('gui-update-' + uuid.uuid4().hex + '\n').encode('ascii')
    for name in all_writes:
        source = safe_path(card, name)
        old = source.read_bytes() if source.is_file() else None
        if old is not None:
            dst = safe_path(backup, 'before/' + name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_checked(dst, old)
        records[name] = {'before_sha256': checksum(old) if old is not None else None,
                         'installed_sha256': checksum(all_writes[name])}
    write_checked(backup / 'backup.json', json.dumps({'files': records}, indent=2).encode())
    try:
        for name, data in all_writes.items():
            if name == RESTART:
                continue
            if volume(str(card))['serial'] != identity['serial']:
                raise OSError('写入期间磁盘已更换')
            write_checked(safe_path(card, name), data)
        verify_backend(lambda name: safe_path(card, name).read_bytes())
        if volume(str(card))['serial'] != identity['serial']:
            raise OSError('写入期间磁盘已更换')
        write_checked(safe_path(card, RESTART), all_writes[RESTART])
    except (OSError, ValueError) as exc:
        raise OSError(f'{exc}\n请保留存储卡，备份位于：{backup}') from exc
    return backup


def export_logs(card, destination, identity):
    logs = safe_path(card, 'nx300m-mod/logs')
    selected = []
    if logs.is_dir():
        for path in logs.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.log', '.txt'):
                if not (getattr(path.stat(), 'st_file_attributes', 0) & 0x400):
                    selected.append(path)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('说明.txt', '日志可能包含相机、网络和电脑路径信息，请检查后再分享。\n未包含照片或完整配置。\n')
        archive.writestr('设备信息.json', json.dumps(identity, ensure_ascii=False, indent=2))
        local_log = BASE / 'installer.log'
        if local_log.is_file() and not local_log.is_symlink():
            archive.write(local_log, 'installer.log')
        for path in selected:
            archive.write(path, 'logs/' + path.name)
    return len(selected)


def catalog(card):
    path = safe_path(card, 'nx300m-mod/looks.txt')
    raw = path.read_bytes()
    lines = raw.decode('utf-8-sig').splitlines()
    rows = [line.split('|') for line in lines if line.strip()]
    if not rows or any(len(r) != 9 or not r[0] or not r[1] for r in rows):
        raise ValueError('LUT 目录格式不受支持。')
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError('LUT 目录存在重复 ID。')
    return raw, rows


def rename_look(card, identity, key, label):
    label = label.strip()
    if not label or len(label.encode('utf-8')) > 120 or any(c in '|\r\n\0' or ord(c) < 32 for c in label):
        raise ValueError('名称不能为空，也不能包含竖线或换行。')
    original, rows = catalog(card)
    match = next((r for r in rows if r[0] == key), None)
    if match is None:
        raise ValueError('风格已不存在，请刷新列表。')
    match[1] = label
    backup = BASE / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-lut-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    if volume(str(card)) != identity or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    write_checked(safe_path(card, 'nx300m-mod/looks.txt'),
                  ('\n'.join('|'.join(row) for row in rows) + '\n').encode('utf-8'))
    return backup


def add_builtin_looks(card, identity):
    original, rows = catalog(card)
    _, builtins = catalog(FIRST/'payload')
    known={row[0] for row in rows}
    additions=[row for row in builtins if row[0] not in known]
    if not additions: return 0, None
    if len(rows)+len(additions)>255:
        raise ValueError('风格列表已满。')
    manifest=json.loads((FIRST/'manifest.json').read_text(encoding='utf-8'))
    pending=[]
    for row in additions:
        relative='nx300m-mod/looks/'+row[0]
        destination=safe_path(card,relative)
        if destination.exists(): raise ValueError('目录已存在但未列入风格列表：'+row[0])
        metadata=json.loads(safe_path(FIRST/'payload',relative+'/look.json').read_text(encoding='utf-8'))
        if metadata.get('source_kind')!='original-procedural' or metadata.get('license')!='CC0-1.0':
            raise ValueError('此安装包不包含可添加的原创风格。')
        for name in ('table.d4lut','look.json'):
            path=relative+'/'+name
            data=safe_path(FIRST/'payload',path).read_bytes()
            if checksum(data)!=manifest['files'][path]['sha256']: raise ValueError('内置风格校验失败。')
            pending.append((path,data))
    backup=BASE/'backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-builtin-'+uuid.uuid4().hex[:6])
    backup.mkdir(parents=True);write_checked(backup/'looks.txt',original)
    for path,data in pending:
        if volume(str(card))!=identity: raise ValueError('存储卡信息已变化。')
        target=safe_path(card,path);target.parent.mkdir(parents=True,exist_ok=True);write_checked(target,data)
    if volume(str(card))!=identity or catalog(card)[0]!=original:
        raise ValueError('存储卡或风格列表发生变化，新风格尚未加入列表。')
    addition=(''.join('|'.join(row)+'\n' for row in additions)).encode('utf-8')
    write_checked(safe_path(card,'nx300m-mod/looks.txt'),original.rstrip(b'\r\n')+b'\n'+addition)
    return len(additions),backup


def import_cube(card, identity, source, label):
    from cube_to_d4 import compile_cube, load_cube
    if BASE.resolve().is_relative_to(card.resolve()):
        raise ValueError('请先把程序完整解压到电脑。')
    label = label.strip()
    if not label or len(label.encode('utf-8')) > 120 or any(c in '|\r\n\0' or ord(c) < 32 for c in label):
        raise ValueError('名称不能为空，也不能包含竖线或换行。')
    original, rows = catalog(card)
    if len(rows) >= 255:
        raise ValueError('风格列表已满，无法继续导入。')
    baseline_path = safe_path(card, 'nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut')
    stock = None
    if baseline_path.exists():
        baseline = baseline_path.read_bytes()
        if len(baseline) != 4380 or checksum(baseline) != '8e90e5a7528928585c86e4fe7e74d689ed96abd6cbb37f94bce234ddca09a07a':
            raise ValueError('原厂组合表不匹配。')
        stock = struct.unpack('<2190H', baseline)
        if any(x > 1023 for x in stock) or any(stock[i] for i in (729, 1459, 2189)):
            raise ValueError('原厂组合表格式错误。')
    source_data = source.read_bytes()
    cube = load_cube(source)
    direct = compile_cube(cube, method='fit')
    still = compile_cube(cube, stock, method='fit') if stock is not None else None
    if volume(str(card))['serial'] != identity['serial'] or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    key = 'import-' + uuid.uuid4().hex[:16]
    lookdir = safe_path(card, 'nx300m-mod/looks/' + key)
    lookdir.mkdir()
    backup = BASE / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-lut-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    write_checked(backup / 'source.cube', source_data)
    metadata = {'id': key, 'label': label, 'source_kind': 'user-cube', 'input_space': 'srgb',
                'output_space': 'srgb', 'native_table': 'table.d4lut',
                'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380},
                'calibration': {'camera': 'NX300M', 'status': 'unmeasured'},
                'compiler': {'method': 'fit', 'tables': {'table.d4lut': checksum(direct)}}}
    tables = [('table.d4lut', direct)]
    if still is not None:
        tables.append(('table-stock3d.d4lut', still))
        metadata['native_stock_3d_table'] = 'table-stock3d.d4lut'
        metadata['compiler']['tables']['table-stock3d.d4lut'] = checksum(still)
    tables.append(('look.json', (json.dumps(metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8')))
    for name, data in tables:
        write_checked(lookdir / name, data)
    if volume(str(card))['serial'] != identity['serial'] or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化；新风格尚未加入目录。')
    line = (key + '|' + label + '|0|0|0|0|0|0|0\n').encode('utf-8')
    write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original.rstrip(b'\r\n') + b'\n' + line)
    return key, backup


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('NX300M MOD 安装与诊断')
        self.geometry('960x650')
        self.minsize(850, 590)
        self.option_add('*Font', ('Microsoft YaHei UI', 10))
        self.configure(bg='#eef2f5')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background='#eef2f5')
        style.configure('TLabel', background='#eef2f5', foreground='#243344')
        style.configure('TButton', padding=(16, 10), background='#ffffff', foreground='#253648', borderwidth=1)
        style.map('TButton', background=[('active', '#dcecf8'), ('disabled', '#e3e8ec')], foreground=[('disabled', '#9daab4')])
        style.configure('Accent.TButton', background='#1479bb', foreground='#ffffff', borderwidth=0)
        style.map('Accent.TButton', background=[('active', '#11689f'), ('disabled', '#9db9ca')], foreground=[('disabled', '#e5eef4')])
        self.drive = tk.StringVar()
        self.status = tk.StringVar(value='请选择相机关机后取出的存储卡。')
        self.checked = None
        side = tk.Frame(self, bg='#243a4d', width=205, padx=24, pady=28)
        side.pack(side='left', fill='y'); side.pack_propagate(False)
        tk.Label(side, text='NX300M', font=('Microsoft YaHei UI', 20, 'bold'), bg='#243a4d', fg='white').pack(anchor='w')
        tk.Label(side, text='MOD 工具箱', font=('Microsoft YaHei UI', 11), bg='#243a4d', fg='#a9c6d8').pack(anchor='w', pady=(2, 36))
        tk.Label(side, text='存储卡安装', font=('Microsoft YaHei UI', 11, 'bold'), bg='#243a4d', fg='#ffffff').pack(anchor='w', pady=(0, 16))
        tk.Button(side, text='LUT 管理', command=self.looks, font=('Microsoft YaHei UI', 11), bg='#243a4d', fg='#d5e6ef', activebackground='#365469', activeforeground='white', relief='flat', bd=0, cursor='hand2').pack(anchor='w', pady=(0, 16))
        tk.Button(side, text='排错日志', command=self.logs, font=('Microsoft YaHei UI', 11), bg='#243a4d', fg='#d5e6ef', activebackground='#365469', activeforeground='white', relief='flat', bd=0, cursor='hand2').pack(anchor='w')
        body = ttk.Frame(self, padding=(32, 26))
        body.pack(side='left', fill='both', expand=True)
        ttk.Label(body, text='相机存储卡', font=('Microsoft YaHei UI', 21, 'bold')).pack(anchor='w')
        ttk.Label(body, text='连接读卡器后，选择相机的存储卡。', foreground='#667c8b').pack(anchor='w', pady=(4, 18))
        row = ttk.Frame(body); row.pack(fill='x')
        self.combobox = ttk.Combobox(row, textvariable=self.drive, state='readonly')
        self.combobox.pack(side='left', fill='x', expand=True, padx=(0, 12), ipady=5)
        self.combobox.bind('<<ComboboxSelected>>', lambda e: self.reset())
        ttk.Button(row, text='刷新', command=self.refresh).pack(side='left')
        ttk.Label(body, text='首次安装', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w', pady=(24, 2))
        ttk.Label(body, text='适用于已刷入 MFW 的 NX300M 1.15。', foreground='#667c8b').pack(anchor='w')
        ttk.Button(body, text='安装 MOD 到存储卡', style='Accent.TButton', command=self.first_install).pack(anchor='w', pady=(10, 0))
        ttk.Label(body, text='管理与维护', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w', pady=(24, 10))
        buttons = ttk.Frame(body); buttons.pack(fill='x')
        ttk.Button(buttons, text='LUT 管理', command=self.looks).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='检查升级', command=self.check).pack(side='left', padx=(0, 8))
        self.install = ttk.Button(buttons, text='升级 MOD', command=self.upgrade, state='disabled')
        self.install.pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='导出日志', command=self.logs).pack(side='left')
        ttk.Button(body, text='卸载 MOD', command=self.uninstall).pack(anchor='w', pady=(10, 0))
        ttk.Label(body, text='操作记录', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(22, 6))
        self.output = tk.Text(body, height=5, wrap='word', state='disabled', relief='flat', borderwidth=0, bg='#ffffff', fg='#344b5b', padx=12, pady=10, font=('Microsoft YaHei UI', 10))
        self.output.pack(fill='both', expand=True)
        self.refresh()

    def say(self, text):
        self.output.configure(state='normal'); self.output.insert('end', text + '\n'); self.output.see('end'); self.output.configure(state='disabled')
        self.status.set(text)
        try:
            with (BASE / 'installer.log').open('a', encoding='utf-8') as log:
                log.write(datetime.now().isoformat(timespec='seconds') + ' ' + text + '\n')
        except OSError:
            pass

    def reset(self):
        self.checked = None; self.install.configure(state='disabled')

    def refresh(self):
        self.reset(); drives = []
        if os.name == 'nt':
            for i in range(26):
                root = chr(65+i) + ':\\'
                try:
                    data = volume(root)
                    drives.append(f"{root}  {data['label'] or '未命名'}  {data['filesystem']}  {data['serial']}")
                except (OSError, ValueError):
                    pass
        self.combobox['values'] = drives
        self.drive.set(drives[0] if len(drives) == 1 else '')
        self.say('已刷新存储卡列表。' if drives else '未发现可移动存储卡，请连接读卡器后刷新。')

    def selected(self):
        value = self.drive.get()
        if not value:
            raise ValueError('请先选择存储卡。')
        root = value[:3]
        return Path(root), volume(root)

    def check(self):
        self.reset()
        try:
            card, identity = self.selected(); prepare(card)
            self.checked = (str(card), identity)
            self.install.configure(state='normal')
            self.say(f"检查通过：{card}  卷号 {identity['serial']}。升级将使用 ABI7 Session 预览。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.say('检查未通过：' + str(exc))

    def first_install(self):
        try:
            card, identity = self.selected()
            prepare_first(card, identity)
            if not messagebox.askyesno('首次安装', f'确认相机已刷入 MFW、已关机，并选对存储卡 {card}（{identity["serial"]}）？\n\n将写入 MOD 文件和启动脚本。卡上已有同名文件时会停止。'):
                return
            self.say('正在写入并校验，请勿拔出存储卡……')
            self.update_idletasks()
            self.attributes('-disabled', True)
            def work():
                try:
                    install_first(card, identity)
                    result = '安装文件已写入并回读。请安全弹出存储卡，放入已刷 MFW 的相机后开机检查。'
                except Exception as exc:
                    result = '首次安装未完成：' + str(exc)
                self.after(0, lambda: finish(result))
            def finish(result):
                self.attributes('-disabled', False)
                self.reset()
                self.say(result)
            threading.Thread(target=work, daemon=True).start()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.say('首次安装检查未通过：' + str(exc))

    def upgrade(self):
        try:
            card, identity = self.selected()
            if not self.checked or (str(card), identity) != self.checked:
                raise ValueError('存储卡信息已变化，请重新检查。')
            if not messagebox.askyesno('确认升级', f"相机已关机，且已选对 {card}（{identity['serial']}）？\n\n将备份并更新 MOD 文件。"): return
            backup = apply(card, identity); self.reset()
            self.say(f'升级完成，写入已回读。备份：{backup}。请安全弹出存储卡。')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.reset(); self.say('升级未完成：' + str(exc))
            messagebox.showerror('升级未完成', str(exc))

    def uninstall(self):
        try:
            card, identity = self.selected()
            if not messagebox.askyesno('卸载 MOD', f'确认相机已关机，并卸载存储卡 {card}（{identity["serial"]}）上的 MOD？\n\n启动脚本和 MOD 文件会移到卡上的备份目录，照片和 MFW 固件不受影响。'):
                return
            backup = uninstall_mod(card, identity)
            self.reset()
            self.say(f'MOD 已从启动入口移除；文件保留在 {backup}。请安全弹出存储卡。')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.say('卸载未完成：' + str(exc))
            messagebox.showerror('卸载未完成', str(exc))

    def logs(self):
        try:
            card, identity = self.selected()
            if not safe_path(card, 'nx300m-mod').is_dir(): raise ValueError('卡上未找到 NX300M MOD 目录。')
            destination = filedialog.asksaveasfilename(title='导出排错日志', defaultextension='.zip', initialfile='NX300M-logs.zip', filetypes=[('ZIP 压缩包', '*.zip')])
            if not destination: return
            if Path(destination).resolve().is_relative_to(card.resolve()): raise ValueError('请把日志保存到电脑。')
            count = export_logs(card, destination, identity)
            self.say(f'已导出 {count} 个日志文件：{destination}。分享前请检查内容。')
        except (OSError, ValueError) as exc:
            self.say('日志导出失败：' + str(exc))

    def looks(self):
        try:
            card, identity = self.selected()
            _, rows = catalog(card)
        except (OSError, ValueError) as exc:
            self.say('无法读取 LUT：' + str(exc)); return
        window = tk.Toplevel(self); window.title('LUT 管理'); window.geometry('800x590'); window.minsize(700, 500)
        window.transient(self); window.grab_set()
        frame = ttk.Frame(window, padding=26); frame.pack(fill='both', expand=True)
        header = ttk.Frame(frame); header.pack(fill='x')
        ttk.Label(header, text='LUT 管理', font=('Microsoft YaHei UI', 20, 'bold')).pack(side='left')
        button = ttk.Button(header, text='导入 .cube', style='Accent.TButton', command=lambda: begin())
        button.pack(side='right')
        title = ttk.Label(frame, text=f'存储卡 {card} · 共 {len(rows)} 款', foreground='#667c8b')
        title.pack(anchor='w', pady=(4, 14))
        search = tk.StringVar()
        search_row = ttk.Frame(frame); search_row.pack(fill='x', pady=(0, 10))
        ttk.Label(search_row, text='搜索').pack(side='left', padx=(0, 10))
        ttk.Entry(search_row, textvariable=search).pack(side='left', fill='x', expand=True, ipady=4)
        list_frame = ttk.Frame(frame); list_frame.pack(fill='both', expand=True)
        listing = ttk.Treeview(list_frame, columns=('name', 'id'), show='headings', selectmode='browse', height=10)
        listing.heading('name', text='风格名称'); listing.heading('id', text='风格 ID')
        listing.column('name', width=280, minwidth=180); listing.column('id', width=290, minwidth=180)
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=listing.yview)
        listing.configure(yscrollcommand=scrollbar.set)
        listing.pack(side='left', fill='both', expand=True); scrollbar.pack(side='right', fill='y')
        self.style_tree = ttk.Style(window)
        self.style_tree.configure('Treeview', rowheight=34, font=('Microsoft YaHei UI', 10), background='#ffffff', fieldbackground='#ffffff')
        self.style_tree.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=7)
        def reload():
            _, current = catalog(card); listing.delete(*listing.get_children())
            query = search.get().strip().casefold()
            for row in current:
                if query in row[1].casefold() or query in row[0].casefold():
                    listing.insert('', 'end', iid=row[0], values=(row[1], row[0]))
            title.configure(text=f'存储卡 {card} · 共 {len(current)} 款')
        search.trace_add('write', lambda *_: reload())
        reload()
        state = tk.StringVar(value='支持 sRGB 3D .cube。导入不改变当前风格；有原厂组合表时会同时生成组合版本。')
        ttk.Label(frame, textvariable=state, wraplength=700, foreground='#667c8b').pack(anchor='w', pady=(12, 0))
        actions = ttk.Frame(frame); actions.pack(fill='x', pady=(12, 0))
        def rename():
            selected = listing.selection()
            if not selected:
                state.set('请先选择需要重命名的风格。'); return
            key = selected[0]
            old = listing.item(key, 'values')[0]
            name = simpledialog.askstring('重命名风格', '输入相机中显示的名称：', initialvalue=old, parent=window)
            if name is None or name.strip() == old: return
            try:
                backup = rename_look(card, identity, key, name)
                reload(); state.set(f'名称已更新。目录备份：{backup}')
            except (OSError, ValueError) as exc:
                state.set('重命名失败：' + str(exc))
        rename_button = ttk.Button(actions, text='重命名', command=rename)
        rename_button.pack(side='left')
        def builtin():
            try:
                count,backup=add_builtin_looks(card,identity)
                reload();state.set(f'已添加 {count} 款内置风格。'+(f'目录备份：{backup}' if backup else ''))
            except (OSError,ValueError,KeyError) as exc:
                state.set('添加失败：'+str(exc))
        builtin_button=ttk.Button(actions,text='添加内置风格',command=builtin)
        builtin_button.pack(side='left',padx=(8,0))
        close_button = ttk.Button(actions, text='关闭', command=window.destroy)
        close_button.pack(side='right')
        def begin():
            filename = filedialog.askopenfilename(parent=window, title='选择 sRGB 3D LUT', filetypes=[('3D LUT', '*.cube')])
            if not filename: return
            name = simpledialog.askstring('风格名称', '输入相机中显示的名称：', initialvalue=Path(filename).stem, parent=window)
            if name is None: return
            button.configure(state='disabled'); rename_button.configure(state='disabled'); close_button.configure(state='disabled'); state.set('正在编译 LUT，请稍候……')
            builtin_button.configure(state='disabled')
            window.protocol('WM_DELETE_WINDOW', lambda: None)
            def work():
                try:
                    key, backup = import_cube(card, identity, Path(filename), name)
                    window.after(0, lambda: done(f'已导入 {key}。备份：{backup}', True))
                except Exception as exc:
                    msg = str(exc)
                    window.after(0, lambda: done('导入失败：' + msg, False))
            threading.Thread(target=work, daemon=True).start()
        def done(text, success):
            window.protocol('WM_DELETE_WINDOW', window.destroy); button.configure(state='normal'); rename_button.configure(state='normal'); close_button.configure(state='normal'); state.set(text)
            builtin_button.configure(state='normal')
            if success: reload()
        window.protocol('WM_DELETE_WINDOW', window.destroy)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--check-converter':
        from cube_to_d4 import compile_cube
        values = [(r, g, b) for b in range(2) for g in range(2) for r in range(2)]
        result = compile_cube((2, (0.,)*3, (1.,)*3, values), method='fit')
        Path(sys.argv[2]).write_text(str(len(result)), encoding='ascii')
    else:
        App().mainloop()
