"""Record the stock getter evidence used by the read-only mode context bridge."""
import io
import json
from pathlib import Path
from elftools.elf.elffile import ELFFile
from inspect_recipe_tables import all_symbols, disassemble
from inspect_mode_enums import write_evidence, CAPTURE_LIBRARY

PROJECT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    enum_report = write_evidence(Path(config['rootfs']) / CAPTURE_LIBRARY)
    library = Path(config['rootfs']) / 'usr/lib/libiq-fw-slpcam.so'
    elf = ELFFile(io.BytesIO(library.read_bytes()))
    symbols = all_symbols(elf)
    getters = {
        'capture_mode_raw': ('_ZNK15CIQDataRepeater14GetCaptureModeEv', 0x104, 0x41),
        'mode3d_raw': ('_ZNK15CIQDataRepeater9Get3DModeEv', 0x12c, 0x4b),
        'smartpro_mode_raw': ('_ZNK15CIQDataRepeater15GetSmartProModeEv', 0x1b0, 0x6c),
    }
    names = [value[0] for value in getters.values()] + [
        '_ZN15CIQDataRepeater12GetParameterEiPi',
        '_ZN10CIpcIfInfo12GetSceneModeEv',
    ]
    assembly, functions = disassemble(elf, symbols, names)
    output = PROJECT / 'outputs/metol/mode-context'
    output.with_suffix('.asm').write_text(assembly, encoding='utf-8')
    report = {
        'source': str(library), 'functions': functions,
        'getters': {field: {'symbol': symbol, 'repeater_data_offset': hex(offset), 'iq_id': hex(iq_id)}
                    for field, (symbol, offset, iq_id) in getters.items()},
        'dial_mode_raw': {'iq_id': '0x49', 'basis': 'CCapParamIf converts USERDATA_DIALMODE (ID 0); this is not the PW/SmartFilter effect mode'},
        'parameter_layout': 'GetParameter loads object+8, then the word at iq_id*4; valid IDs 0..0x72',
        'scope': 'NX300M getter slots and target-native P/A/S/M name-to-IQ mapping. No cross-camera mode enum equality is asserted.',
        'target_pasm_mapping_verified': enum_report['target_pasm_mapping_verified'],
        'target_mode_enums': 'outputs/metol/mode-context-enums.json',
        'picture_wizard': {'userdata_id': '0x10', 'iq_id': '0x2c'},
        'picture_wizard_raw': {'iq_id': '0x2c', 'basis': 'CCapParamIf converts USERDATA_PW (ID 0x10)'},
        'source_mode_mapping_verified': False,
        'runtime_evidence': 'outputs/metol/chroma-mode-context.json',
    }
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
