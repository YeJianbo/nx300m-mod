"""Build the manual one-shot CS RAM blob; never loads it into a camera."""
import json
from pathlib import Path
import shutil
import struct
import subprocess
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/controls/manual-lens/cs-ram'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / 'manual_lens_cs_ram.elf'
    subprocess.run([
        shutil.which('clang'), '--target=arm-linux-gnueabi', '-march=armv7-a',
        '-marm', '-mfloat-abi=soft', '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror',
        '-ffreestanding', '-fpie', '-fvisibility=hidden', '-fno-builtin',
        '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
        '-nostdlib', '-static', '-fuse-ld=lld', '-Wl,--no-undefined',
        '-Wl,--build-id=none', '-Wl,-T,' + str(ROOT / 'runtime/manual_lens_cs_ram.ld'),
        str(ROOT / 'runtime/manual_lens_cs_ram.c'), '-o', str(target),
    ], check=True)
    with target.open('rb') as stream:
        elf = ELFFile(stream)
        if any(s['sh_type'] in ('SHT_REL', 'SHT_RELA', 'SHT_DYNAMIC') for s in elf.iter_sections()):
            raise ValueError('CS RAM blob must have no runtime relocation or dynamic linking')
        words = struct.unpack('<16I', elf.get_section_by_name('.header').data())
        symbols = {s.name: s for s in elf.get_section_by_name('.symtab').iter_symbols()}
        if words[:2] != (0x4e584353, 1) or words[3] != words[4] or words[4] % 4096:
            raise ValueError('CS RAM header layout differs')
        if words[11] != 200 or int(symbols['nx_cs_ram_config']['st_size']) != words[11]:
            raise ValueError('CS RAM config ABI differs')
        image = bytearray(words[2])
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_type'] != 'SHT_NOBITS':
                at = int(section['sh_addr'])
                image[at:at + section['sh_size']] = section.data()
    binary = OUT / 'manual_lens_cs_ram.bin'
    binary.write_bytes(image)
    names = ('magic', 'abi', 'blob_size', 'code_size', 'data_offset', 'data_size',
             'update_entry', 'common_entry', 'info_entry', 'onoff_entry', 'config_offset', 'config_size')
    report = dict(zip(names, words))
    report.update(binary=binary.relative_to(ROOT).as_posix(), no_runtime_relocations=True,
                  no_libc=True, production_admission_enabled=False,
                  target_executed=False, camera_or_card_accessed=False)
    (OUT / 'blob-build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
