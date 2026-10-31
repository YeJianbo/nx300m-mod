"""Run only the original NX300M SLP reader in an isolated read-only ARM harness.

Never executes stock upgrade main. Does not create an installable SLP or use a
camera. Windows entry point uses the project's Conda Python; Linux needs only
unshare, mount, chroot, timeout and the existing static user-mode QEMU.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import time

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/firmware-validation'
STOCK = ROOT / 'work/stock-1.15/nx300m.bin'
MAIN = 0xb33c
PARSER = 0x9b68
# End addresses exclude literal pools. These exact functions were reviewed.
FUNCTIONS = {'header_reader': (0x96b8, 0x9818), 'version_string': (0x9838, 0x991c),
             'slp_reader': (0x9b68, 0xa0a0), 'crc_update': (0xa0e4, 0xa120),
             'crc_stream': (0xa128, 0xa1f4)}
ALLOWED_IMPORTS = {'memset', 'strchr', 'open64', 'read', 'close', 'sprintf',
                   'fprintf', 'strlen', 'strcpy', 'memcpy', 'strcat', 'printf',
                   'fopen64', 'fread', 'strncmp', 'fseek', 'fclose', 'feof',
                   'ferror', 'fwrite'}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def wsl(path):
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def prepare(firmware):
    OUT.mkdir(parents=True, exist_ok=True)
    source = json.loads((ROOT / 'work/stock-1.15/source.json').read_text(encoding='utf-8'))
    assert (source['model'], source['version'], source['zip_entry_crc_verified_on_complete_read']) == ('NX300M', '1.15', True)
    stock_sha = digest(STOCK)
    assert stock_sha == source['local_binary_sha256'] and STOCK.stat().st_size == source['binary_size']
    original = (ROOT / 'outputs/deployment/stock/usr/sbin/fw_upgrade').read_bytes()
    elf = ELFFile(io.BytesIO(original))
    assert elf['e_machine'] == 'EM_ARM' and elf['e_type'] == 'ET_EXEC' and elf['e_entry'] == 0x8d30

    def offset(address, size=4):
        for seg in elf.iter_segments():
            if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] <= address and address + size <= seg['p_vaddr'] + seg['p_filesz']:
                return seg['p_offset'] + address - seg['p_vaddr']
        raise ValueError('Address outside file-backed LOAD: ' + hex(address))

    def word(address):
        return struct.unpack_from('<I', original, offset(address))[0]

    assert word(0x8d50) == 0xe59f000c and word(0x8d64) == MAIN
    assert elf.get_section_by_name('.init_array').data() == struct.pack('<I', 0x8dac)
    assert elf.get_section_by_name('.fini_array').data() == struct.pack('<I', 0x8d90)
    assert word(0x8dd8) == 0  # frame_dummy's optional registration target is absent.
    assert word(0x154ac) == 0  # No .jcr registration object.
    symbols = list(elf.get_section_by_name('.dynsym').iter_symbols())
    assert next(s for s in symbols if s.name == '__gmon_start__')['st_shndx'] == 'SHN_UNDEF'
    plt = elf.get_section_by_name('.plt')
    imports = {plt['sh_addr'] + 20 + i * 12: symbols[r['r_info_sym']].name
               for i, r in enumerate(elf.get_section_by_name('.rel.plt').iter_relocations())}
    cs = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    calls, assembly = [], []
    for name, (start, end) in FUNCTIONS.items():
        assembly.append('\n## ' + name)
        code = original[offset(start, end-start):offset(start, end-start)+end-start]
        instructions = list(cs.disasm(code, start))
        assert len(instructions) * 4 == len(code)
        for ins in instructions:
            annotation = ''
            instruction_word = struct.unpack('<I', ins.bytes)[0]
            is_branch = instruction_word & 0x0e000000 == 0x0a000000
            if is_branch and instruction_word & 0x01000000:
                assert instruction_word >> 28 != 15, 'Unreviewed BLX immediate'
                target = int(ins.op_str[1:], 16)
                target_name = imports.get(target)
                if target_name:
                    assert target_name in ALLOWED_IMPORTS, 'Unreviewed imported call: ' + target_name
                else:
                    target_name = next((n for n, (a, _) in FUNCTIONS.items() if a == target), None)
                    assert target_name, 'Unreviewed local callee: ' + hex(target)
                calls.append({'caller': name, 'address': hex(ins.address), 'target': hex(target), 'name': target_name})
                annotation = ' ; ' + target_name
            elif is_branch:
                assert start <= int(ins.op_str[1:], 16) < end, 'Branch escapes reviewed function'
            assert ins.mnemonic != 'blx'
            if ins.mnemonic.startswith('bx'):
                assert ins.op_str == 'lr', 'Unreviewed indirect branch'
            assembly.append(f'{ins.address:08x} {ins.mnemonic:8} {ins.op_str}{annotation}')
    # Resolve the parser's PIC-relative fopen mode literal, not a guessed mode.
    got_base = 0x9be8 + word(0xa0a0)
    mode_addr = (got_base + word(0xa0a8)) & 0xffffffff
    mode = original[offset(mode_addr, 3):offset(mode_addr, 3)+3]
    assert mode == b'rb\0', repr(mode)
    assert word(0x970c) == 0xe3a01000  # header_reader open64(path, O_RDONLY).
    # ARM AAPCS main(argc, argv): return 64 unless argc==3; otherwise preserve
    # 8-byte stack alignment, call untouched parser(argv[1],argv[2],1), return.
    branch_at = MAIN + 28
    branch = 0xeb000000 | (((PARSER - branch_at - 8) // 4) & 0xffffff)
    words = [0xe3500003, 0x13a00040, 0x112fff1e, 0xe92d4010,
             0xe5910004, 0xe5911008, 0xe3a02001, branch, 0xe8bd8010]
    stub = struct.pack('<9I', *words)
    patched = bytearray(original)
    patch_offset = offset(MAIN, len(stub))
    patched[patch_offset:patch_offset+len(stub)] = stub
    differences = [{'file_offset': i, 'address': hex(MAIN+i-patch_offset), 'before': a, 'after': b}
                   for i, (a, b) in enumerate(zip(original, patched)) if a != b]
    assert all(patch_offset <= d['file_offset'] < patch_offset + len(stub) for d in differences)
    for start, end in FUNCTIONS.values():
        assert patched[offset(start):offset(start)+end-start] == original[offset(start):offset(start)+end-start]
    (OUT / 'parser-callgraph.asm').write_text('\n'.join(assembly) + '\n', encoding='utf-8')
    crt_ranges = [(0x8b24,0x8b30),(0x8d30,0x8ddc),(0xb870,0xb8e4)]
    crt_lines = []
    cs.skipdata = True
    for start, end in crt_ranges:
        for ins in cs.disasm(original[offset(start):offset(start)+end-start], start):
            crt_lines.append(f'{ins.address:08x} {ins.mnemonic:8} {ins.op_str}')
    (OUT / 'crt-review.asm').write_text('\n'.join(crt_lines) + '\n', encoding='utf-8')
    (OUT / 'main-stub.bin').write_bytes(stub)
    (OUT / 'main-stub.asm').write_text('\n'.join(f'{i.address:08x} {i.mnemonic:8} {i.op_str}' for i in cs.disasm(stub, MAIN))+'\n', encoding='utf-8')
    stage = OUT / 'runtime-files'
    stage.mkdir(exist_ok=True)
    (stage / 'slp-readonly.arm').write_bytes(patched)
    provenance = []
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        members = {m.name: m for m in archive}
        def resolve(name):
            for _ in range(16):
                m = members[name]
                if m.isfile():
                    return name, archive.extractfile(m).read()
                if m.issym():
                    name = posixpath.normpath(m.linkname.lstrip('/') if m.linkname.startswith('/') else posixpath.join(posixpath.dirname(name), m.linkname))
                elif m.islnk():
                    name = m.linkname.lstrip('/')
                else:
                    raise ValueError('Unsupported TAR member: ' + name)
            raise ValueError('Dependency link cycle')
        member, data = resolve('usr/sbin/fw_upgrade')
        assert data == original
        pending = ['lib/ld-linux.so.3']
        dynamic = elf.get_section_by_name('.dynamic')
        needed = [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == 'DT_NEEDED']
        assert needed == ['libpthread.so.0', 'libgcc_s.so.1', 'libc.so.6']
        pending.extend('lib/' + n for n in needed)
        seen = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            member, data = resolve(name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            depelf = ELFFile(io.BytesIO(data))
            dynamic = depelf.get_section_by_name('.dynamic')
            depneeded = [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == 'DT_NEEDED'] if dynamic else []
            assert set(depneeded) <= {'ld-linux.so.3', 'libpthread.so.0', 'libgcc_s.so.1', 'libc.so.6'}
            pending.extend('lib/' + n for n in depneeded)
            provenance.append({'destination': name, 'stock_member': member, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    shutil.copyfile(ROOT / 'work/qemu/qemu-arm-static', stage / 'qemu-arm-static')
    expected_files = {p['destination'] for p in provenance} | {'slp-readonly.arm', 'qemu-arm-static'}
    assert {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()} == expected_files
    assert not any(p.is_symlink() for p in stage.rglob('*'))
    bad = OUT / 'first-partition-bad-crc.fixture.bin'
    shutil.copyfile(STOCK, bad)
    with bad.open('r+b') as stream:
        stream.seek(68)
        crc = struct.unpack('<I', stream.read(4))[0]
        stream.seek(68)
        stream.write(struct.pack('<I', crc ^ 1))
    # Compare complete fixture to stock: exactly one differing byte, in CRC.
    diff_offsets = []
    with STOCK.open('rb') as a, bad.open('rb') as b:
        cursor = 0
        while left := a.read(1024*1024):
            right = b.read(len(left))
            assert len(right) == len(left)
            if left != right:
                diff_offsets.extend(cursor+i for i,(x,y) in enumerate(zip(left,right)) if x != y)
            cursor += len(left)
        assert not b.read(1)
    assert diff_offsets == [68]
    metadata = {'stock_source': source, 'stock_sha256_verified': stock_sha,
                'selected_firmware': str(firmware), 'selected_firmware_sha256': digest(firmware),
                'original_elf_sha256': hashlib.sha256(original).hexdigest(),
                'harness_sha256': hashlib.sha256(patched).hexdigest(),
                'stock_elf_equal_to_tar': True, 'elf_entry_unchanged': hex(elf['e_entry']),
                'original_main': hex(MAIN), 'parser': hex(PARSER), 'crc_enabled': True,
                'patch': {'offset': patch_offset, 'address': hex(MAIN), 'length': len(stub),
                          'before_hex': original[patch_offset:patch_offset+len(stub)].hex(),
                          'after_hex': stub.hex(), 'all_differing_bytes': differences},
                'reviewed_function_ranges': {n:[hex(a),hex(b)] for n,(a,b) in FUNCTIONS.items()},
                'calls': calls, 'fopen_mode': 'rb', 'header_open_flags': 0,
                'init_array': ['0x8dac'], 'fini_array': ['0x8d90'], 'gmon_undefined': True,
                'jcr_object': 0, 'frame_dummy_indirect_target': 0,
                'dependencies': provenance,
                'bad_crc_fixture': {'bytes': bad.stat().st_size, 'differing_offsets': diff_offsets,
                                    'crc_before': crc, 'crc_after': crc^1, 'sha256': digest(bad)}}
    save('harness.json', metadata)
    return stage, bad, metadata


def run_case(label, firmware, model, stage):
    mount_root = OUT / 'probe-root'
    mount_root.mkdir(exist_ok=True)
    script = f'''set -eu
echo "SLP_PROBE_PHASE setup_start $(date +%s)" >&2
mount --make-rprivate /
root={shlex.quote(wsl(mount_root))}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(wsl(stage))}/. "$root"/
echo "SLP_PROBE_PHASE dependencies_copied $(date +%s)" >&2
mkdir -p "$root/input" "$root/dev"
touch "$root/input/firmware.bin" "$root/dev/null" "$root/dev/urandom"
chmod -R a+rX "$root"
chmod 755 "$root/slp-readonly.arm" "$root/qemu-arm-static"
mount --bind {shlex.quote(wsl(firmware))} "$root/input/firmware.bin"
mount -o remount,bind,ro "$root/input/firmware.bin"
mount --bind /dev/null "$root/dev/null"
mount --bind /dev/urandom "$root/dev/urandom"
mount -o remount,ro "$root"
echo 'SLP_PROBE uid=65534 root=readonly input=readonly devices=null,urandom namespaces=mount,net,ipc,pid' >&2
echo "SLP_PROBE_PHASE arm_start $(date +%s)" >&2
set +e
env -i PATH=/usr/bin:/bin timeout --kill-after=5 90 /usr/sbin/chroot --userspec=65534:65534 "$root" /qemu-arm-static -cpu cortex-a9 -L / -strace -d in_asm -E LD_BIND_NOW=1 /slp-readonly.arm /input/firmware.bin {shlex.quote(model)}
code=$?
echo "SLP_PROBE_PHASE arm_finished $(date +%s) exit=$code" >&2
exit "$code"
'''
    (OUT / (label+'-host.sh')).write_text(script, encoding='utf-8', newline='\n')
    started = time.monotonic()
    try:
        result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '--cd', '/', '-e',
                                 'timeout', '--kill-after=5', '120', 'unshare',
                                 '--mount', '--net', '--ipc', '--pid', '--fork', '--kill-child', '--mount-proc', 'sh', '-s'],
                                input=script.encode(), capture_output=True, timeout=135)
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b'', error.stderr or b''
        (OUT / (label+'.stdout.log')).write_bytes(stdout)
        (OUT / (label+'.stderr.log')).write_bytes(stderr)
        phases = re.findall(r'^SLP_PROBE_PHASE (.+)$', stderr.decode('utf-8','replace'), re.M)
        save(label+'-execution.json', {'exit_code':None, 'timeout':'Windows subprocess 135 seconds',
                                     'elapsed_seconds':round(time.monotonic()-started,3),
                                     'last_phase':phases[-1] if phases else 'before guest setup_start; guest process state unknown',
                                     'stdout_bytes':len(stdout),'stderr_bytes':len(stderr)})
        raise RuntimeError(f'{label}: outer WSL timeout; partial logs preserved; last phase: {phases[-1] if phases else "guest setup not observed"}') from error
    (OUT / (label+'.stdout.log')).write_bytes(result.stdout)
    (OUT / (label+'.stderr.log')).write_bytes(result.stderr)
    stderr = result.stderr.decode('utf-8', 'replace')
    phases = re.findall(r'^SLP_PROBE_PHASE (.+)$', stderr, re.M)
    save(label+'-execution.json', {'exit_code':result.returncode,
                                 'elapsed_seconds':round(time.monotonic()-started,3),
                                 'phases':phases, 'stdout_bytes':len(result.stdout), 'stderr_bytes':len(result.stderr)})
    if result.returncode in (124,137):
        raise RuntimeError(f'{label}: Linux timeout exit {result.returncode}; last phase: {phases[-1] if phases else "guest setup not observed"}; raw logs preserved')
    addresses = sorted(set(int(x,16) for x in re.findall(r'^0x([0-9a-fA-F]+):', stderr, re.M)))
    original_main_reached = [hex(a) for a in addresses if MAIN+36 <= a < 0xb870]
    syscall_lines = [line for line in stderr.splitlines() if re.match(r'^\d+ [a-zA-Z_][\w]*\(', line)]
    opens = [line for line in syscall_lines if re.match(r'^\d+ (?:open|openat|creat)\(', line)]
    forbidden = [line for line in syscall_lines if re.match(r'^\d+ (?:ioctl|execve|unlink|rename|mount|reboot|socket|connect)\(', line)]
    writes = [line for line in syscall_lines if re.match(r'^\d+ (?:write|writev|pwrite64)\(', line)]
    allowed_code = list(FUNCTIONS.values()) + [(0x8b24,0x8ddc),(0xb870,0xb8e4),(MAIN,MAIN+36)]
    outside_review = [hex(a) for a in addresses if 0x8000 <= a < 0x16000 and not any(lo <= a < hi for lo,hi in allowed_code)]
    assert PARSER in addresses, 'No original parser instruction trace'
    assert not original_main_reached and not forbidden and not outside_review, 'Unexpected execution or syscall'
    assert all('O_RDONLY' in line for line in opens), 'Non-read-only open observed'
    assert all(re.match(r'^\d+ write\([12],', line) for line in writes), 'Write outside stdout/stderr'
    record = {'case': label, 'model_argument': model, 'exit_code': result.returncode,
              'parser_return': 0 if result.returncode == 0 else -1 if result.returncode == 255 else None,
              'original_parser_reached': True, 'crc_helper_reached': 0xa128 in addresses,
              'crc_update_reached': 0xa0e4 in addresses, 'original_main_body_reached': original_main_reached,
              'opens': opens, 'forbidden_syscalls': forbidden, 'target_syscall_lines': len(syscall_lines),
              'writes':writes, 'outside_reviewed_code':outside_review,
              'stdout': result.stdout.decode('utf-8', 'replace'),
              'diagnostics': [line for line in stderr.splitlines() if '[FW]' in line],
              'instruction_addresses': [hex(a) for a in addresses if 0x8000 <= a < 0x16000]}
    save(label+'-result.json', record)
    print(json.dumps({k:v for k,v in record.items() if k in ('case','exit_code','parser_return','original_parser_reached','crc_helper_reached','stdout')},ensure_ascii=False), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=STOCK, help='Optional local candidate for the same read-only parser check')
    args = parser.parse_args()
    firmware = args.firmware.resolve(strict=True)
    stage, bad, metadata = prepare(firmware)
    # Baseline and negative controls always run, including for later candidates.
    cases = [run_case('stock-accept', STOCK, 'NX300M', stage),
             run_case('wrong-model-reject', STOCK, 'NX300M_WRONG', stage),
             run_case('bad-crc-reject', bad, 'NX300M', stage)]
    assert [c['exit_code'] for c in cases] == [0,255,255], 'Parser acceptance/control mismatch'
    assert cases[0]['crc_update_reached'] and cases[2]['crc_update_reached']
    if firmware != STOCK.resolve():
        cases.append(run_case('candidate-parser', firmware, 'NX300M', stage))
    # Verify original artifacts after execution as well as before it.
    assert digest(STOCK) == metadata['stock_sha256_verified']
    assert hashlib.sha256((ROOT / 'outputs/deployment/stock/usr/sbin/fw_upgrade').read_bytes()).hexdigest() == metadata['original_elf_sha256']
    report = {'status': 'stock parser accepts verified stock; both negative controls reject',
              'cases': cases, 'stock_input_and_original_elf_unchanged': True,
              'isolation': {'namespaces':['mount','net','ipc','pid'], 'uid':65534,
                            'root_readonly':True,'input_bind_readonly':True,
                            'devices':['null','urandom'], 'proc_in_chroot':False,
                            'upgrade_tools_in_chroot':False},
              'scope': 'Original SLP reader and CRC code only under user-mode QEMU; not upgrade main, bootloader, flash writer, or physical camera acceptance',
              'parser_limits':['Reads partition payloads sequentially by length; declared offset fields are not checked',
                               'Does not require final EOF; acceptance is not a complete SLP structural validation'],
              'firmware_package_built':False, 'camera_connected':False, 'flash_attempted':False}
    save('report.json', report)
    if firmware != STOCK.resolve():
        raise SystemExit(cases[-1]['exit_code'])


if __name__ == '__main__':
    main()
