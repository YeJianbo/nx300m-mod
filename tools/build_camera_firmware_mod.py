"""Build the ARM camera firmware mod core library."""
import shutil
import subprocess
from pathlib import Path
import io
import struct
from elftools.elf.elffile import ELFFile
from inspect_recipe_tables import all_symbols, read_symbol
from build_manual_lens_wifi import write_wifi_stock_header

PROJECT = Path(__file__).resolve().parents[1]
compiler = shutil.which('clang')
if not compiler:
    raise SystemExit('clang is required')

output = PROJECT / 'outputs/controls/camera_firmware_mod.so'
output.parent.mkdir(parents=True, exist_ok=True)

# Only these reviewed NX300M 1.15 A32 entries may be relocated. Runtime checks
# compare each complete function; these bytes never modify an on-disk firmware.
specs = [
    ('MODE_EVENT', 'usr/bin/di-camera-app-nx300', '_Z25UI_Event_ModeDial_ManagerP15tag_EVENT_UI_IF', 0x1a6cbc, 1596),
    ('ANIM_START', 'usr/bin/di-camera-app-nx300', '_ZN15CAPPGUIModeView15ani_timer_startEd', 0x9e328, 84),
    ('MODE_DRAW', 'usr/bin/di-camera-app-nx300', '_ZN15CAPPGUIModeView12DrawModeDialEv', 0x9d36c, 1396),
    ('CUSTOM', 'usr/bin/di-camera-app-nx300', '_Z23gUI_Key_Liveview_CustomP15tag_EVENT_UI_IF', 0x17d468, 32),
    ('ISO_SET', 'usr/lib/libcapture-fw-slpcam-nx300.so', '_ZN22CDataManagementService11SetUserDataEii', 0x21b388, 256),
]
headers, evidence = [], []
for label, relative, name, address, size in specs:
    source = PROJECT/'outputs/deployment/stock'/relative
    if not source.is_file():
        source = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')/relative
    elf = ELFFile(io.BytesIO(source.read_bytes()))
    symbol = all_symbols(elf)[name]
    if (symbol['st_value'], symbol['st_size']) != (address, size):
        raise ValueError(f'Unreviewed stock entry: {name}')
    data, _, _ = read_symbol(elf, symbol)
    first, second = struct.unpack_from('<II', data)
    if (first, second) not in ((0xe92d4800, 0xe28db004),
                               (0xe92d4810, 0xe28db008),
                               (0xe92d4830, 0xe28db00c)):
        raise ValueError(f'Cannot relocate the first eight bytes of {name}')
    # Shared-library addresses are load-relative; full bytes still must match.
    runtime_address = 0 if label == 'ISO_SET' else address
    headers.append(f'#define NX_{label}_ADDRESS 0x{runtime_address:x}u\n'
                   f'#define NX_{label}_SYMBOL "{name}"\n'
                   f'static const unsigned char nx_{label.lower()}_stock[]={{'+
                   ','.join(f'0x{byte:02x}' for byte in data)+'};\n')
    evidence.append({'symbol': name, 'address': hex(address), 'size': size,
                     'relocated_prologue': data[:8].hex(), 'source': relative})

# The standalone candidate and this core use one reviewed function generator
# and one runtime patch implementation; neither may widen the other's gate.
evidence.append(write_wifi_stock_header(output.parent))
(output.parent/'camera_firmware_mod_stock.h').write_text(''.join(headers), encoding='ascii')
import json
(output.parent/'firmware-entry-evidence.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')

cmd = [
    compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
    '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
    '-Wl,-z,nocombreloc', '-Ioutputs/controls', 'runtime/camera_firmware_mod.c', '-o', str(output),
]
print("Compiling camera_firmware_mod.so with:", " ".join(cmd))
subprocess.run(cmd, cwd=PROJECT, check=True)
print(f"Successfully compiled: {output} (size: {output.stat().st_size} bytes)")
