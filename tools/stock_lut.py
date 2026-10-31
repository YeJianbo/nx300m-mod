"""Read the factory NX300M 1.15 still LUT from the verified stock runtime copy."""
from functools import lru_cache
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def factory_3d_lut():
    config = json.loads((PROJECT/'project.local.json').read_text(encoding='utf-8'))
    path = Path(config['rootfs'])/'usr/lib/libiq-fw-slpcam.so'
    elf = ELFFile(io.BytesIO(path.read_bytes()))
    symbols = {s.name: s for s in elf.get_section_by_name('.dynsym').iter_symbols()}
    planes = []
    for channel in 'RGB':
        symbol = symbols['_ZN12CIpcs3DLutD47m_s'+channel+'LutE']
        if symbol['st_size'] != 1460: raise ValueError('Unexpected stock LUT symbol size')
        address = symbol['st_value']
        segment = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and s['p_vaddr'] <= address and address+1460 <= s['p_vaddr']+s['p_filesz'])
        elf.stream.seek(segment['p_offset']+address-segment['p_vaddr'])
        plane = struct.unpack('<730H', elf.stream.read(1460))
        if max(plane) > 1023 or plane[-1]: raise ValueError('Invalid stock 3D LUT data')
        planes.extend(plane)
    return tuple(planes)
