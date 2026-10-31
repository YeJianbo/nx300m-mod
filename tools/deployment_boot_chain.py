"""Bounded read-only normal boot trace; writes only app-loader/boot-chain evidence."""
import argparse
import io
import difflib
from collections import Counter
import json
from pathlib import Path
import re
import struct
import os
import sys
import subprocess
import tarfile

os.environ.setdefault('CONDA_PREFIX', sys.prefix)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/app-loader/boot-chain'
ROOTFS = (
    '.profile', '.ashrc', 'etc/profile', 'etc/bash.bashrc', 'etc/ashrc',
    'etc/inittab', 'etc/~inittab', 'etc/init.d/rcS',
    'etc/rc.d/rc.sysinit', 'etc/rc.local', 'etc/init.d/rc.local',
    'etc/profile.d/Xorg.sh', 'etc/profile.d/isf.sh', 'opt/etc/passwd',
    'opt/home_org/root/.profile', 'opt/home_org/root/.bashrc',
    'opt/home_org/root/.ashrc',
    'opt/home_org/root/.e/e/applications/startup/.order',
    'opt/home_org/root/.xinitrc', 'opt/home/root/.profile', 'opt/home/root/.bashrc',
    'opt/home/root/.xinitrc', 'usr/etc/X11/xinitrc', 'usr/etc/X11/Xsession',
    'usr/bin/startx', 'usr/bin/go.sh', 'usr/bin/hib2.sh', 'etc/fstab',
    'etc/mdev.conf', 'etc/rc.d/rc.leavesleep', 'etc/rc.d/rc.entersleep',
)
TOKENS = re.compile(r'di-camera-app|(?:^|[/\s])go\.sh|hib2\.sh|bootargs|bootcmd|resume=|mtdblock9|software_resume|swsusp|/etc/profile|/etc/inittab|/etc/init.d/rcS')


def save(relative, data):
    path = OUT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def hits(data):
    if b'\0' in data:
        return []
    return [{'line': i, 'text': line[:1200]} for i, line in enumerate(data.decode('utf-8', 'replace').splitlines(), 1)
            if TOKENS.search(line)]


def rootfs_trace():
    result = {'members': [], 'absent': [], 'links': [], 'x11_udev_candidates': []}
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as tar:
        names = {m.name: m for m in tar}
        for name in ROOTFS:
            member = names.get(name)
            if not member:
                result['absent'].append(name)
                continue
            item = {'path': name, 'bytes': member.size, 'mode': oct(member.mode), 'link': member.linkname}
            if member.isfile():
                data = tar.extractfile(member).read()
                save('rootfs/' + name, data)
                item['hits'] = hits(data)
            result['members'].append(item)
        for name in ('root', 'home', 'opt/home', 'sbin/init', 'bin/sh', 'usr/bin/di-camera-app'):
            member = names.get(name)
            if member:
                result['links'].append({'path': name, 'link': member.linkname, 'type': member.type.decode()})
        # Only the startup-directory successors, not a repeat of all-program scans.
        for name, member in names.items():
            if not member.isfile() or member.size > 100000:
                continue
            if not name.startswith(('usr/etc/X11/', 'etc/udev/rules.d/')):
                continue
            data = tar.extractfile(member).read()
            found = hits(data)
            result['x11_udev_candidates'].append({'path': name, 'bytes': member.size, 'hits': found})
            if found:
                save('rootfs/' + name, data)
        busybox = tar.extractfile('bin/busybox').read()
        result['busybox_strings'] = [
            {'offset': m.start(), 'text': m.group().decode('ascii')}
            for m in re.finditer(rb'[ -~]{5,}', busybox)
            if any(x in m.group() for x in (b'BusyBox v', b'/etc/inittab', b'/etc/init.d/rcS', b'/etc/profile', b'/.profile'))
        ]
    return result


def source_trace(archive):
    result = {'archive': str(archive), 'archive_bytes': archive.stat().st_size,
              'scope': 'one streaming metadata pass; only busybox init/shell, model packaging, bootloader and kernel resume sources',
              'top_level_counts': {}, 'bootloader_names': [], 'busybox_names': [],
              'packaging_names': [], 'saved': [], 'content_hits': []}
    groups = Counter()
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            name = member.name.removeprefix('./')
            parts = name.split('/')
            groups['/'.join(parts[:3])] += 1
            lower = name.lower()
            boot = any(x in lower for x in ('u-boot', 'uboot', '/bootloader/'))
            busy = 'busybox' in lower
            model = name.startswith('TIZEN/project/NX300M/')
            packaging = model and (name.endswith(('.sh', '.spec', '.mk', '.conf', '.config'))
                                   or Path(name).name in ('Makefile', 'inittab', 'profile', '.profile', '.bashrc', 'rcS', 'rc.local'))
            if boot:
                result['bootloader_names'].append(name)
            if busy and (name.endswith(('.tar.gz', '.tar.bz2', '.spec', '.config', 'init.c', 'ash.c'))):
                result['busybox_names'].append(name)
            if packaging:
                result['packaging_names'].append(name)
            wanted = (
                busy and (name.endswith(('/init/init.c', '/shell/ash.c', '/.config', '.spec')))
                or packaging
                or boot and name.endswith(('.c', '.h', '.sh', '.mk', '.cfg', '.conf', 'Makefile'))
                or name in ('TIZEN/packages_nx300m/linux/kernel/power/hibernate.c',
                            'TIZEN/packages_nx300m/linux/kernel/power/swap.c',
                            'TIZEN/packages_nx300m/linux/init/main.c',
                            'TIZEN/packages_nx300m/linux/arch/arm/configs/drime4_nx300m_defconfig')
            )
            if not wanted or not member.isfile() or member.size > 500000:
                continue
            data = tar.extractfile(member).read()
            found = hits(data)
            important = busy and name.endswith(('/init/init.c', '/shell/ash.c', '/.config', '.spec'))
            important |= name.endswith(('/kernel/power/hibernate.c', '/kernel/power/swap.c', '/linux/init/main.c', '/drime4_nx300m_defconfig'))
            if found or important:
                save('gpl/' + name, data)
                result['saved'].append({'path': name, 'bytes': len(data)})
            if found:
                result['content_hits'].append({'path': name, 'hits': found})
    result['top_level_counts'] = dict(groups)
    return result


def firmware_bootloader():
    with (ROOT / 'work/stock-1.15/nx300m.bin').open('rb') as stream:
        stream.seek(64 + 2 * 16)
        size, crc, offset, magic = struct.unpack('<4I', stream.read(16))
        stream.seek(offset)
        data = stream.read(size)
    needles = (b'root=', b'init=', b'resume', b'snapshot', b'cold boot', b'Kernel Parameter', b'console=', b'noresume')
    return {'partition': 'D4_PNLBL.bin', 'firmware_offset': offset, 'bytes': size,
            'strings': [{'partition_offset': m.start(), 'firmware_offset': offset + m.start(),
                         'text': m.group().decode('ascii')}
                        for m in re.finditer(rb'[ -~]{5,}', data) if any(x in m.group() for x in needles)],
            'scope': 'actual 1.15 bootloader strings; no inferred control flow from strings alone'}


def normal_kernel_evidence():
    # Reuse the already extracted normal kernel; do not unpack either image.
    path = ROOT / 'outputs/quickshare/stock-1.15-kernel-50ac20.bin'
    if not path.is_file():
        return {'status': 'previous normal kernel extraction unavailable; not repeated'}
    data = path.read_bytes()
    return {'source': path.relative_to(ROOT).as_posix(), 'bytes': len(data),
            'strings': [{'kernel_offset': m.start(), 'text': m.group().decode('ascii')}
                        for m in re.finditer(rb'[ -~]{5,}', data)
                        if any(x in m.group() for x in (b'Linux version ', b'console=ttyAMA0', b'/sbin/init',
                                                       b'Failed to load hibernation image', b'/dev/mtdblock9'))],
            'embedded_config': 'not available in prior extraction; GPL defconfig is source evidence only'}


def exact_sources(archive, names):
    pending = set(names)
    found = []
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            name = member.name.removeprefix('./')
            if name not in pending or not member.isfile():
                continue
            data = tar.extractfile(member).read()
            save('gpl/' + name, data)
            found.append({'path': name, 'bytes': len(data)})
            pending.remove(name)
            if not pending:
                break
    return {'requested': names, 'found': found, 'absent': sorted(pending)}


def snapshot_trace(archive):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
    from elftools.elf.elffile import ELFFile
    selected = ('usr/sbin/fw_upgrade', 'usr/lib/libfirmware-upgrade.so',
                'usr/lib/libhibernation-slpcam-0.so', 'usr/bin/di-camera-app-nx300')
    binaries = {}
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as tar:
        for name in selected:
            data = tar.extractfile(name).read()
            binaries[name] = data
            save('snapshot/elf/' + Path(name).name, data)
    result = {'scope': 'static original binaries/source only; no updater, app or hibernation execution',
              'elfs': [], 'app_hib_callers': [], 'fw_snapshot_request_callers': []}
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True
    for name, data in binaries.items():
        elf = ELFFile(io.BytesIO(data))
        symbols = []
        for sname in ('.dynsym', '.symtab'):
            section = elf.get_section_by_name(sname)
            if section:
                symbols += list(section.iter_symbols())
        definitions = {s['st_value']: s.name for s in symbols if s['st_shndx'] != 'SHN_UNDEF' and s.name}
        plt = elf.get_section_by_name('.plt')
        rel = elf.get_section_by_name('.rel.plt')
        dyn = list(elf.get_section_by_name('.dynsym').iter_symbols())
        if plt and rel:
            for i, r in enumerate(rel.iter_relocations()):
                definitions[plt['sh_addr'] + 20 + 12 * i] = dyn[r['r_info_sym']].name + '@plt'
        ranges = []
        if name.endswith('/fw_upgrade'):
            ranges += [(0xa240, 0xf8, 'system_status_wrapper'), (0xa6a4, 0x160, 'snapshot_request_and_reboot'),
                       (0xa804, 0xb5c, 'partition_update_loop'), (0xb360, 0x1a0, 'updater_main')]
        elif 'libhibernation' in name:
            ranges += [(s['st_value'], s['st_size'], s.name) for s in symbols
                       if s.name in ('hib', 'hib_post', 'hib_has_hibdata') and s['st_size']]
        elif 'libfirmware-upgrade' in name:
            ranges += [(s['st_value'], s['st_size'], s.name) for s in symbols
                       if s.name == 'fw_clear_snapshot_request' and s['st_size']]
        elif name.endswith('/di-camera-app-nx300'):
            ranges += [(0x1717d8, 0x160, 'main_and_create_callback_registration'),
                       (0x171604, 0x84, 'create_callback_to_UI_Camera_Init')]
        if name.endswith('/fw_upgrade') or name.endswith('/di-camera-app-nx300'):
            text = elf.get_section_by_name('.text')
            code, base = text.data(), text['sh_addr']
            funcs = [s for s in symbols if s['st_info']['type'] == 'STT_FUNC' and s['st_size']]
            wanted = {a for a, label in definitions.items() if label in ('hib@plt', 'hib_post@plt')}
            if name.endswith('/fw_upgrade'):
                wanted = {0xa6a4}
            for offset in range(0, len(code) - 3, 4):
                word = struct.unpack_from('<I', code, offset)[0]
                if word & 0x0f000000 != 0x0b000000 or word >> 28 == 15:
                    continue
                displacement = word & 0xffffff
                if displacement & 0x800000:
                    displacement -= 0x1000000
                address = base + offset
                target = address + 8 + displacement * 4
                if target not in wanted:
                    continue
                func = next((s for s in funcs if s['st_value'] <= address < s['st_value'] + s['st_size']), None)
                item = {'address': hex(address), 'target': hex(target), 'label': definitions.get(target),
                        'function': func.name if func else None}
                result['app_hib_callers' if 'di-camera' in name else 'fw_snapshot_request_callers'].append(item)
                ranges.append((func['st_value'], func['st_size'], func.name) if func else
                              (max(base, address - 0x100), 0x200, 'caller_context'))
        lines = []
        for start, size, label in sorted(set(ranges)):
            seg = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and
                       s['p_vaddr'] <= start < s['p_vaddr'] + s['p_filesz'])
            off = seg['p_offset'] + start - seg['p_vaddr']
            lines.append(f'## {label} {start:#x} size={size:#x}')
            for ins in decoder.disasm(data[off:off + size], start):
                annotation = ''
                if ins.mnemonic in ('bl', 'blx', 'b') and ins.op_str.startswith('#0x'):
                    annotation = definitions.get(int(ins.op_str[1:], 16), '')
                lines.append(f'{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}' + (' ; ' + annotation if annotation else ''))
        save('snapshot/' + Path(name).name + '.asm', ('\n'.join(lines) + '\n').encode())
        result['elfs'].append({'path': name, 'bytes': len(data), 'ranges': len(ranges)})
    fw = binaries['usr/sbin/fw_upgrade']
    fwelf = ELFFile(io.BytesIO(fw))
    def fwread(address, size=4):
        segment = next(s for s in fwelf.iter_segments() if s['p_type'] == 'PT_LOAD' and
                       s['p_vaddr'] <= address < s['p_vaddr'] + s['p_filesz'])
        offset = segment['p_offset'] + address - segment['p_vaddr']
        return fw[offset:offset + size]
    def word(address):
        return struct.unpack('<I', fwread(address))[0]
    pic = (0xa86c + word(0xa85c + 8 + 0x9e4)) & 0xffffffff
    table = (pic + word(0xa8d0 + 8 + 0x97c)) & 0xffffffff
    result['updater_partition_table'] = []
    for i in range(7):
        address = table + i * 0x3c
        raw = fwread(address, 0x3c)
        result['updater_partition_table'].append({
            'address': hex(address), 'mtd': raw[0],
            'ubi_index': struct.unpack_from('<i', raw, 4)[0],
            'image_index': struct.unpack_from('<i', raw, 8)[0],
            'name': raw[12:52].split(b'\0')[0].decode(),
            'raw_field_52': word(address + 52), 'raw_field_56': word(address + 56),
        })
    result['snapshot_invalidation'] = {
        'snapshot_record': '0x15728: mtd=9, image_index=-1, field52=0, field56=1',
        'branch': '0xab0c -> 0xab40 checks field52==0 -> 0xb02c loads mtd -> 0xb038 system wrapper',
        'command': 'flash_erase /dev/mtd9 0 0',
        'main_success_condition': '0xb454 calls fw_do_upgrade; 0xb458..0xb45c requires return 0; 0xb460 calls snapshot request routine',
        'status': 'actual original updater control flow; full updater has not been executed',
    }
    for filename, start, size, target in (
        ('libhibernation-slpcam-0.so', 0x15d8, 0xd28, 'libhib-literals.txt'),
        ('libfirmware-upgrade.so', 0x2aac, 0x68, 'fw-clear-request-literals.txt'),
        ('di-camera-app-nx300', 0x154528, 0xe8, 'app-hib-prepare-literals.txt'),
        ('fw_upgrade', 0xa6a4, 0x160, 'fw-request-literals.txt'),
    ):
        command = [sys.executable, '-X', 'utf8', str(ROOT / 'tools/deployment_pic_literals.py'),
                   str(OUT / 'snapshot/elf' / filename), '--range', hex(start), hex(size)]
        process = subprocess.run(command, capture_output=True)
        save('snapshot/' + target, process.stdout)
        if process.returncode:
            raise RuntimeError(f'Literal decoding failed ({process.returncode}): {process.stderr.decode("utf-8", "replace")}')
    with (ROOT / 'work/stock-1.15/nx300m.bin').open('rb') as stream:
        stream.seek(5201412)
        boot = stream.read(63580)
    lines = []
    for start, size in ((0x9750, 0x2b0), (0xa2dc, 0x78), (0x109c, 0x40)):
        lines.append(f'## D4_PNLBL partition offset {start:#x}; runtime address = 0x70200000 + offset')
        for ins in decoder.disasm(boot[start:start + size], start):
            annotation = ''
            if ins.mnemonic.startswith('ldr') and '[pc, #' in ins.op_str:
                match = re.search(r'\[pc, #(0x[0-9a-f]+)\]', ins.op_str)
                if match:
                    literal = ins.address + 8 + int(match.group(1), 16)
                    value = struct.unpack_from('<I', boot, literal)[0]
                    annotation = f' literal[{literal:#x}]={value:#x}'
                    if 0x70200000 <= value < 0x70200000 + len(boot):
                        raw = boot[value - 0x70200000:].split(b'\0', 1)[0]
                        if raw and all(32 <= c < 127 or c in (10, 13) for c in raw):
                            annotation += ' ' + json.dumps(raw.decode())
            lines.append(f'{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}{annotation}')
    save('snapshot/D4_PNLBL-direct-resume.asm', ('\n'.join(lines) + '\n').encode())
    result['bootloader_direct_resume'] = {
        'source': 'work/stock-1.15/nx300m.bin:D4_PNLBL.bin', 'partition_firmware_offset': 5201412,
        'runtime_base': '0x70200000', 'snapshot_header': '0xd0000000', 'resume_field_offset': '0xfd8',
        'decode_resume_field': 'partition 0x99d4..0x99f0 assembles 32-bit field from four bytes',
        'call': '0x99f4 BL 0xa2dc -> 0xa2e4 BL 0x109c',
        'direct_jump': '0x109c MOV sp,r0; after cache/MMU setup 0x10d0 MOV r3,sp; 0x10d4 MOV pc,r3',
        'second_jump_in_wrapper': '0xa340 MOV pc,r4',
        'status': 'actual bootloader direct resume branch decoded; not executed',
    }
    kernel_names = ['TIZEN/packages_nx300m/linux/' + p for p in (
        'kernel/power/snapshot.c', 'mm/filemap.c', 'mm/memory.c', 'mm/vmscan.c',
        'include/linux/mm.h', 'include/linux/page-flags.h',
        'arch/arm/kernel/sleep.S')]
    missing = [p for p in kernel_names if not (OUT / 'gpl' / p).is_file()]
    if missing:
        result['source_supplement'] = exact_sources(archive, missing)
    else:
        result['source_supplement'] = {'reused': kernel_names}
    result['lifecycle'] = {
        'update_success': 'fw_upgrade 0xb454 calls updater; return 0 at 0xb458..0xb45c reaches 0xb460 snapshot request',
        'request': '0xa744 touch; 0xa764 echo 0 + two sync; 0xa7b4 system reboot -f',
        'rcS': 'request count 10 is removed; otherwise remaining request count increments and hib2.sh runs',
        'hib2': 'sets HIB=a, remounts / read-only, starts X, runs hib_exjob -r, resolves app symlink, launches app without args in background',
        'app_no_arguments': 'main sets 0x4dbb60=1; UI_Camera_Init calls 0x154528; hib_register_exmem return >=0 reaches hib@plt at 0x154600',
        'hib_condition': 'libhib 0x1618 getenv("HIB"); only NULL skips to 0x21c4; no value==a test',
        'hib_sequence': 'echo reboot > /sys/power/disk; unchecked system umount /mnt/mmc; open power/state; unmount/detach ubi1; write disk length 4',
        'resume': 'original write returns on restore; 0x188c rtc_s; library reattaches/loads original pref data; app continues without exec',
        'request_clear': 'UI_Camera_Init: common-menu DB id0xfa nonzero and hib_has_extradata() and hib_has_hibdata() -> fw_clear_snapshot_request()',
        'clear_function': 'libfirmware-upgrade 0x2aac unlinks request, touches fw/upgrade_done and syncs, sets PCACHE=REFRESH',
    }
    result['file_backed_page_lifecycle'] = {
        'saved_pages': 'snapshot.c:953-976 saves resident nonfree nonforbidden pages; does not exclude all clean file pages',
        'reclaim_before_save': 'hibernate.c:435-473 shrinks memory before hibernation_snapshot; vmscan.c:2879 sets may_unmap=1; mapped file pages may be reclaimed',
        'later_fault': 'filemap.c:1603+ uses vma->vm_file->f_mapping; absent/not-uptodate pages read backing; read failure may return VM_FAULT_SIGBUS',
        'vendor_readahead': 'extended_snapshot.c:799+ filp_open(fname,O_RDONLY) plus do_readahead; recorded paths/ranges do not embed a substitute filesystem file',
        'conclusion': 'saved resident pages can resume without immediate file reads; all future code faults are not guaranteed resident, so original mapping needs stable accessible backing',
        'persistent_rootfs': 'built-in SO keeps backing independent of SD; backing rootfs/SO must remain unchanged until snapshot is regenerated',
        'scope': 'vendor Linux 3.5 source and original user-space binaries; no physical SD removal or full kernel binary equivalence test',
    }
    save('snapshot/report.json', (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode())
    return result


def persistent_candidate():
    from build_stock_app_loader import PERSISTENT_HOOKS, LAUNCH_PROFILES, persistent_launch_files, verify_native
    base = 'persistent-candidate/'
    changes = []
    scripts = []
    for item in persistent_launch_files():
        target = base + 'rootfs' + item['destination']
        save(target, item['content'])
        record = {k: v for k, v in item.items() if k not in ('content', 'original')}
        record['bytes'] = len(item['content'])
        changes.append(record)
        if item['destination'].endswith(('.sh', 'nx300m-camera-launch')):
            scripts.append(OUT / target)
        if 'original' in item:
            name = item['destination'].lstrip('/')
            # Keep exact stock restore inputs beside the candidate, never in
            # its rootfs payload and never execute the original boot scripts.
            save(base + 'original/' + name, item['original'])
            patch = ''.join(difflib.unified_diff(item['original'].decode().splitlines(True),
                                               item['content'].decode().splitlines(True),
                                               fromfile='a/' + name, tofile='b/' + name))
            save(base + Path(name).name + '.patch', patch.encode())
    copied = []
    for name, origin in PERSISTENT_HOOKS.items():
        data = verify_native(ROOT / origin)
        destination = base + 'rootfs/usr/lib/nx300m-mod/' + name
        save(destination, data)
        copied.append({'source': origin, 'destination': '/usr/lib/nx300m-mod/' + name, 'bytes': len(data)})
        changes.append({'source': origin, 'destination': '/usr/lib/nx300m-mod/' + name,
                        'change': 'new', 'bytes': len(data),
                        'metadata': {'mode': '0o644', 'uid': 0, 'gid': 0}})
    checks = []
    for path in scripts:
        wsl = '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]
        process = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', '/bin/sh', '-n', wsl], capture_output=True)
        checks.append({'path': path.relative_to(ROOT).as_posix(), 'exit_code': process.returncode,
                       'stderr': process.stderr.decode('utf-8', 'replace')})
    report = {
        'status': 'review-only persistent rootfs candidate; no firmware/UBIFS install or snapshot execution',
        'activation_connection': 'stock update -> fresh snapshot -> hib2 app call; registered camera desktop -> nx300m-camera-launch -> original app; automatic non-update cold-boot caller still unconfirmed',
        'preload_location': '/usr/lib/nx300m-mod', 'resource_root': '/mnt/mmc/nx300m-mod',
        'hook_count': len(copied),
        'launch_policy': {'default_profile': 'stock', 'profiles': LAUNCH_PROFILES,
                          'builtin_profile_file': '/usr/lib/nx300m-mod/launch-profile',
                          'sd_profile_override': '/mnt/mmc/nx300m-mod/launch-profile',
                          'disabled_markers': ['/usr/lib/nx300m-mod/DISABLED', '/mnt/mmc/nx300m-mod/DISABLED'],
                          'applies_at': 'next app exec only; no hot unload or snapshot rollback',
                          'unknown_profile': 'original app and inherited environment',
                          'missing_selected_hook': 'original app and inherited environment'},
        'service_payload_requirements': ['ram-start.sh', 'mfw-ram-stage.sh', 'runtime-files.list',
                                         'start.sh', 'stop.sh', 'common.sh', 'service.sh',
                                         'bin/nx-remote-controller.arm', 'bin/nx-quickshare-armv7',
                                         'web_root/index.html'],
        'restore_originals': ['original' + item['destination'] for item in persistent_launch_files()
                              if 'original' in item],
        'sd_presence_required_for_executable_code': False,
        'original_arguments_and_background_operator_preserved': True,
        'other_original_script_bytes_preserved': True, 'hooks': copied, 'shell_syntax': checks,
        'rootfs_changes': changes, 'shared_helper': '/usr/lib/nx300m-mod/preload.sh',
        'original_app_and_symlink_unchanged': True,
        'remaining': ['automatic non-update cold-boot request/exec source', 'actual snapshot creation and resume with built-in hooks',
                      'SD resource behavior and hardware functions', 'rootfs/SLP packaging and updater acceptance',
                      'regenerate snapshot whenever built-in SO or backing rootfs changes'],
    }
    save(base + 'report.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    if any(c['exit_code'] for c in checks):
        raise RuntimeError('Persistent candidate shell syntax check failed')
    return report


def power_source_trace(archive):
    """Read only the vendor extended snapshot source subtree."""
    prefix = 'TIZEN/packages_nx300m/linux/kernel/power/'
    records = []
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            name = member.name.removeprefix('./')
            if not name.startswith(prefix) or not member.isfile() or member.size > 500000:
                continue
            if not (name.endswith(('.c', '.h')) or Path(name).name == 'Makefile'):
                continue
            path = OUT / 'gpl' / name
            if not path.is_file():
                save('gpl/' + name, tar.extractfile(member).read())
            records.append({'path': name, 'bytes': member.size})
    result = {'scope': prefix, 'members': records}
    save('snapshot/power-source-manifest.json', (json.dumps(result, indent=2) + '\n').encode())
    return result


def startup_gap_trace():
    """Follow only the previously unresolved launcher edge and snapshot callback order."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
    from elftools.elf.elffile import ELFFile

    prefix = 'cold-entry/'
    selected = (
        'etc/rc.d/init.d', 'etc/init.d/launchpad_run',
        'usr/etc/X11/xinitrc',
        'opt/share/applications/com.samsung.di-camera-app-nx300.desktop',
        'opt/share/preload_list.txt',
        'usr/bin/launchpad_preloading_preinitializing_daemon',
        'usr/bin/di-camera-app-nx300', 'usr/lib/libappcore-efl.so.1.1',
        'usr/lib/libelementary.so.1.0.99', 'lib/libc-2.13.so',
    )
    data = {}
    members = []
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        for name in selected:
            member = archive.getmember(name)
            item = {'path': name, 'bytes': member.size, 'link': member.linkname}
            if member.isfile():
                data[name] = archive.extractfile(member).read()
                save(prefix + name, data[name])
            members.append(item)

    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True

    def functions(name, wanted):
        raw = data[name]
        elf = ELFFile(io.BytesIO(raw))
        symbols = []
        for section_name in ('.dynsym', '.symtab'):
            section = elf.get_section_by_name(section_name)
            if section:
                symbols.extend(section.iter_symbols())
        labels = {s['st_value']: s.name for s in symbols if s['st_value'] and s.name}
        plt, rel = elf.get_section_by_name('.plt'), elf.get_section_by_name('.rel.plt')
        if plt and rel:
            dyn = list(elf.get_section_by_name('.dynsym').iter_symbols())
            for index, relocation in enumerate(rel.iter_relocations()):
                labels[plt['sh_addr'] + 20 + index * 12] = dyn[relocation['r_info_sym']].name + '@plt'
        lines, records, seen = [], [], set()
        for symbol in symbols:
            if symbol.name not in wanted or not symbol['st_size'] or symbol['st_value'] in seen:
                continue
            start, size = symbol['st_value'], symbol['st_size']
            seen.add(start)
            segment = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and
                           s['p_vaddr'] <= start < s['p_vaddr'] + s['p_filesz'])
            offset = segment['p_offset'] + start - segment['p_vaddr']
            lines.append(f'## {symbol.name} {start:#x} size={size:#x}')
            calls = []
            for ins in decoder.disasm(raw[offset:offset + size], start):
                label = ''
                if ins.mnemonic in ('bl', 'blx', 'b'):
                    label = labels.get(int(ins.op_str[1:], 16), '') if ins.op_str.startswith('#0x') else ''
                    calls.append({'address': hex(ins.address), 'instruction': ins.mnemonic,
                                  'target': ins.op_str, 'label': label})
                lines.append(f'{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}' +
                             (' ; ' + label if label else ''))
            records.append({'name': symbol.name, 'address': hex(start), 'bytes': size, 'calls': calls})
        target = prefix + Path(name).name + '.asm'
        save(target, ('\n'.join(lines) + '\n').encode())
        return {'path': name, 'assembly': target, 'functions': records}

    traces = [
        functions('usr/bin/di-camera-app-nx300', {'main'}),
        functions('usr/lib/libappcore-efl.so.1.1', {'appcore_efl_main'}),
        functions('usr/lib/libelementary.so.1.0.99', {'elm_run'}),
        functions('lib/libc-2.13.so', {'daemon'}),
    ]
    kernel_path = ROOT / 'outputs/quickshare/stock-1.15-kernel-50ac20.bin'
    kernel = kernel_path.read_bytes()
    desktop = data['opt/share/applications/com.samsung.di-camera-app-nx300.desktop'].decode()
    result = {
        'scope': 'read-only exact stock TAR members and reused normal kernel; no app, updater, hibernation or device execution',
        'primary_result': 'desktop launcher entry names /usr/bin/di-camera-app directly and bypasses go.sh; no boot-time camera launch request source established',
        'stock_members': members,
        'launcher_edge': {
            'xinitrc': '/usr/etc/X11/xinitrc: if /tmp/alaunch is absent, call /etc/rc.d/init.d/launchpad_run',
            'resolved_script': '/etc/rc.d/init.d -> /etc/init.d; /etc/init.d/launchpad_run exists',
            'script_commands': ['/usr/bin/launchpad_preloading_preinitializing_daemon <space padding> &', '/usr/bin/ac_daemon &'],
            'camera_desktop': 'opt/share/applications/com.samsung.di-camera-app-nx300.desktop',
            'camera_exec': next(line.split('=', 1)[1] for line in desktop.splitlines() if line.startswith('Exec=')),
            'preload_list': data['opt/share/preload_list.txt'].decode().splitlines(),
            'automatic_camera_request_found': False,
            'go_sh_caller_found': False,
            'meaning': 'a registered camera launch would bypass go.sh; registration and a launcher process do not establish an automatic cold-boot request',
        },
        'normal_kernel_targeted_check': {
            'source': kernel_path.relative_to(ROOT).as_posix(), 'bytes': len(kernel),
            'needles': [{'needle': needle.decode(), 'count': kernel.count(needle)}
                        for needle in (b'go.sh', b'di-camera-app')],
            'meaning': 'no direct command-name evidence in this reused kernel; not a proof against constructed commands or external requests',
        },
        'arm_traces': traces,
        'snapshot_order': {
            'main_daemon': 'app 0x1717ec..0x1717f4: daemon(1,1); original libc daemon forks and exits parent without exec',
            'create_pointer': 'app 0x171834..0x17183c stores create callback 0x171604 at fp-0x30, offset4 of ops at fp-0x34',
            'appcore_call': 'app 0x171918 passes ops at fp-0x34 as r3; 0x171924 calls appcore_efl_main',
            'create_dispatch': 'libappcore-efl appcore_efl_main 0x1a64 loads ops+4; 0x1a74 BLX r3 calls create synchronously',
            'hib_chain': 'create 0x17166c -> UI_Camera_Init; no-argument branch 0x153dc0 -> 0x154528; successful exmem registration reaches hib at 0x154600',
            'outer_loop': 'libappcore-efl 0x1b18 calls elm_run only after create returns; original elm_run calls ecore_main_loop_begin',
            'scope': 'outer appcore callback order only; does not claim no nested loop in every untraced library call',
        },
        'preload_snapshot_conditions': {
            'maps_before_hib': 'if the preload loader and app startup succeed, the seven SO mappings exist before main and are inherited by daemon fork; traced app-to-hib path does not exec a replacement process',
            'snapshot_not_observed': True,
            'what_is_not_proved': ['actual successful image creation', 'actual saved VMA/page content', 'ready markers or service timer registered at snapshot time', 'resume and later file faults on physical hardware'],
            'backing_condition': 'resident file pages may be saved, reclaimed or later faulted; built-in SO/backing must remain stable for the image lifetime',
            'old_snapshot': 'an image created before preload installation retains its old process mappings; changing the script alone does not retroactively add SOs',
            'regeneration_connection': 'reuse established original updater mtd9 invalidation -> snapshot request -> cold rcS -> hib2 candidate; stock SLP acceptance does not itself prove this path was executed',
        },
        'remaining_minimal_evidence': ['one non-update boot app parent/exec or launcher request record',
                                       'actual newly generated snapshot and resume mapping evidence'],
    }
    save(prefix + 'report.json', (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-gpl', action='store_true', help='Reuse the already saved GPL pass')
    parser.add_argument('--gpl-member', action='append', default=[], help='Supplement only an exact known source member')
    parser.add_argument('--snapshot', action='store_true', help='Trace only updater/hib and the direct resume branch')
    parser.add_argument('--snapshot-power-sources', action='store_true', help='Read only kernel/power source subtree')
    parser.add_argument('--persistent-candidate', action='store_true', help='Derive shared internal-rootfs preloading for hib2 and camera desktop')
    parser.add_argument('--persistent-only', action='store_true', help='Refresh persistent launch files and the nested report without repeating static boot analysis')
    parser.add_argument('--startup-gap', action='store_true', help='Only follow the bounded desktop-launcher and appcore snapshot-order edges')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.persistent_only:
        candidate = persistent_candidate()
        path = OUT / 'report.json'
        report = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        report['persistent_candidate'] = candidate
        if 'findings' in report:
            report['findings']['candidate_connection'] = candidate['activation_connection']
        save('report.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
        print(json.dumps(candidate, ensure_ascii=False, indent=2))
        return
    if args.startup_gap:
        result = startup_gap_trace()
        print(json.dumps({'report': 'outputs/deployment/app-loader/boot-chain/cold-entry/report.json',
                          'primary_result': result['primary_result'],
                          'launcher_edge': result['launcher_edge'],
                          'snapshot_order': result['snapshot_order']}, ensure_ascii=False, indent=2))
        return
    result = {'scope': 'normal boot only; vImage updater not reread; no scripts/apps executed',
              'rootfs': rootfs_trace()}
    if args.skip_gpl:
        result['gpl'] = json.loads((OUT / 'report.json').read_text(encoding='utf-8'))['gpl']
    else:
        manifest = json.loads((ROOT / 'work/vendor-source/SOURCE.json').read_text(encoding='utf-8'))
        result['gpl'] = source_trace(Path(manifest['archive']))
    if args.gpl_member:
        result['gpl']['exact_supplement'] = exact_sources(Path(result['gpl']['archive']), args.gpl_member)
    result['firmware_bootloader'] = firmware_bootloader()
    result['normal_kernel'] = normal_kernel_evidence()
    result['gpl_stock_comparison'] = []
    for name in ('etc/profile', 'etc/init.d/rcS', 'usr/bin/go.sh', 'usr/bin/hib2.sh'):
        stock = OUT / 'rootfs' / name
        gpl = OUT / 'gpl/TIZEN/project/NX300M/image/rootfs' / name
        if stock.is_file() and gpl.is_file():
            result['gpl_stock_comparison'].append({'path': name, 'stock_bytes': stock.stat().st_size,
                                                   'gpl_bytes': gpl.stat().st_size,
                                                   'bytes_equal': stock.read_bytes() == gpl.read_bytes()})
    result['findings'] = {
        'normal_app_exec_caller_found': False,
        'sd_mounted_before_app_confirmed': False,
        'source_default_without_inittab': 'SYSINIT /etc/init.d/rcS, then ASKFIRST login shells; not a runlevel traversal',
        'profile_xinitrc_successors': 'no app command in the inspected stock profile, root dotfiles or /usr/etc/X11/xinitrc',
        'gpl_bootloader_snapshot_path': 'Main.c: default snapshot mode; valid image -> jump_to_resume; absent/failed image -> cold kernel + ATAG',
        'actual_1_15_bootloader_strings': 'matching cold/snapshot diagnostics and rootfs bootargs with/without noresume; strings alone do not prove complete binary flow',
        'candidate_connection': 'go.sh remains a caller-unconfirmed candidate; hib2.sh remains the confirmed snapshot creation app command',
        'remaining_evidence': ['actual boot log or bootloader ATAG/cmdline', 'normal app parent/exec trace',
                               'SD mount completion relative to app exec', 'snapshot creation/resume environment and mapping lifetime'],
    }
    if args.snapshot:
        result['snapshot'] = snapshot_trace(Path(result['gpl']['archive']))
    if args.snapshot_power_sources:
        result['power_sources'] = power_source_trace(Path(result['gpl']['archive']))
    if args.persistent_candidate:
        result['persistent_candidate'] = persistent_candidate()
    if (OUT / 'snapshot/report.json').is_file():
        result['findings']['actual_direct_resume_decoded'] = True
        result['findings']['updater_snapshot_invalidation_decoded'] = True
        result['findings']['update_snapshot_app_entry_connected_static'] = True
    if (OUT / 'persistent-candidate/report.json').is_file():
        result['findings']['candidate_connection'] = json.loads(
            (OUT / 'persistent-candidate/report.json').read_text(encoding='utf-8'))['activation_connection']
    save('report.json', (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(json.dumps({'report': (OUT / 'report.json').relative_to(ROOT).as_posix(),
                      'rootfs_found': len(result['rootfs']['members']),
                      'gpl_saved': len(result['gpl']['saved']),
                      'actual_bootloader_bytes': result['firmware_bootloader']['bytes'],
                      'findings': result['findings']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
