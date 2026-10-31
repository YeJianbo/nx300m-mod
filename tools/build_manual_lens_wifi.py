"""Build the minimal opt-in NX300M 1.15 no-lens Wi-Fi gate candidate."""
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from inspect_recipe_tables import all_symbols, read_symbol

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens'


def write_wifi_stock_header(directory):
    """Both candidate modules must verify the identical complete stock function."""
    relative = 'usr/bin/di-camera-app-nx300'
    source = PROJECT / 'outputs/deployment/stock' / relative
    if not source.is_file():
        source = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15') / relative
    name = '_Z31UI_Check_WiFi_ModeChange_Enablei'
    address, size, site, kmount_site = 0x1cc870, 1312, 0x1cccb8, 0x1ccd08
    elf = ELFFile(io.BytesIO(source.read_bytes()))
    symbol = all_symbols(elf)[name]
    if (elf['e_machine'], elf['e_type']) != ('EM_ARM', 'ET_EXEC'):
        raise ValueError('Expected the reviewed non-PIE ARM camera executable')
    if (symbol['st_value'], symbol['st_size']) != (address, size):
        raise ValueError('Unreviewed NX300M Wi-Fi admission function')
    data, _, _ = read_symbol(elf, symbol)
    offset = site - address
    if struct.unpack_from('<II', data, offset - 4) != (0xe3530001, 0x0a000006):
        raise ValueError('Unreviewed Lensconnection comparison or branch')
    kmount_offset = kmount_site - address
    if struct.unpack_from('<II', data, kmount_offset - 4) != (0xe3530000, 0x0a000006):
        raise ValueError('Unreviewed Kmount comparison or branch')
    if (site & ~4095) != (kmount_site & ~4095) or site % 4 or kmount_site % 4:
        raise ValueError('The reviewed aligned Wi-Fi gates must share one page')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'manual_lens_wifi_stock.h').write_text(
        f'#define NX_WIFI_GATE_ADDRESS 0x{address:x}u\n'
        f'#define NX_WIFI_GATE_SYMBOL "{name}"\n'
        f'#define NX_WIFI_GATE_OFFSET {offset}u\n'
        '#define NX_WIFI_GATE_REPLACEMENT 0x9a000006u\n'
        f'#define NX_WIFI_KMOUNT_OFFSET {kmount_offset}u\n'
        '#define NX_WIFI_KMOUNT_REPLACEMENT 0xea000006u\n'
        'static const unsigned char nx_wifi_gate_stock[]={' +
        ','.join(f'0x{byte:02x}' for byte in data) + '};\n', encoding='ascii')
    return {'symbol': name, 'address': hex(address), 'size': size,
            'instruction_address': hex(site), 'original': '0600000a',
            'opt_in_replacement': '0600009a',
            'kmount_instruction_address': hex(kmount_site),
            'kmount_original': '0600000a', 'kmount_opt_in_replacement': '060000ea',
            'verified_comparisons': ['0xe3530001', '0xe3530000'],
            'scope': 'UI Wi-Fi admission: Lensconnection 0 or 1 and Kmount only',
            'retained_checks': ['Lenslock', 'storage', 'busy', 'RVF lens events'],
            'source': relative}


def main():
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    evidence = write_wifi_stock_header(OUT)
    target = OUT / 'manual-lens-wifi.so'
    subprocess.run([
        compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
        '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
        '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
        '-Wl,-z,nocombreloc', '-I' + str(OUT), 'runtime/manual_lens_wifi.c',
        '-o', str(target),
    ], cwd=PROJECT, check=True)
    (OUT / 'wifi-guard-evidence.json').write_text(json.dumps({
        'module': str(target), 'bytes': target.stat().st_size,
        'enabled_by': 'NX300M_MANUAL_LENS_WIFI=1 at camera process startup',
        'function': evidence,
        'identity_guard': 'camera executable basename, fixed symbol address and complete function bytes',
        'status_symbol': 'nx_manual_lens_wifi_status',
        'status_values': {'0': 'disabled', '1': 'not_camera', '2': 'stock_mismatch',
                          '3': 'mprotect_failed', '4': 'connection_and_kmount_installed'},
        'writes_firmware_storage': False, 'physical_rvf_verified': False, 'deployed': False,
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
