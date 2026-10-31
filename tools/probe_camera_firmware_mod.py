"""Inspect firmware bridge ELF exports; does not run QEMU or a camera."""
import json
from pathlib import Path
from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]
LIB_SO = PROJECT / 'outputs/controls/camera_firmware_mod.so'
EXPECTED = ('nx_switch_mode_dial', 'ecore_main_loop_begin')
RETIRED_AE = ('_Z17AeGetDeltaEVMultiP16tagSAeStatisticsP15tagSAeDebugInfoiiiiii',
              '_ZN9CAeInfoIF16calcTvMaxCaptureEv')


def inspect_library():
    with LIB_SO.open('rb') as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM':
            raise ValueError('Expected little-endian ELF32 ARM library')
        exports = sorted(symbol.name for symbol in elf.get_section_by_name('.dynsym').iter_symbols()
                         if symbol.name and symbol['st_shndx'] != 'SHN_UNDEF')
    missing = sorted(set(EXPECTED) - set(exports))
    if missing:
        raise ValueError(f'Missing defined exports: {missing}')
    duplicated = sorted(set(RETIRED_AE) & set(exports))
    if duplicated:
        raise ValueError(f'Retired AE hooks still exported: {duplicated}')
    return {'build': 'passed', 'defined_exports': 'passed', 'runtime': 'not_run',
            'camera_hardware': 'not_run', 'image_effects': 'not_run',
            'retired_ae_exports_absent': True,
            'library': str(LIB_SO), 'size_bytes': LIB_SO.stat().st_size,
            'exported_symbols': exports}


def main():
    result = inspect_library()
    output = PROJECT / 'outputs/controls/camera-firmware-mod-validation.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
