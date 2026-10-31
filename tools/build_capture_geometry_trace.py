"""Verify stock split hook ABI and build a separate observational trace library.

This never deploys the library, edits a running namespace or builds device shims.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]
STOCK = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15/usr/lib/libcapture-fw-slpcam-nx300.so')
OUT = PROJECT / 'outputs/firmware-runtime'
SPLIT = '_ZN7CCpoMnF10bayerSplitEP9CCapImageS1_Pv'
SET = '_ZN13CDDWrapperMnF10SetOpParamEP15stMnF_Geo_Param'
FINISH = '_ZN13CDDWrapperMnF15FinishOperationEv'
VIRTUAL = '_ZN9CCapImage17setVirtualAddressEPv'
PP_START = 'cf_pp_capture_frame_read_write_start'


def main():
    stock_paths = ['/opt/nx300m-lab/stock-1.15/usr/lib/'+name for name in
                   ('libcapture-fw-slpcam.so', 'libcapture-fw-slpcam-nx300.so')]
    identity = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'readlink', '-f', *stock_paths],
                              capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(identity) == 2 and identity[0] == identity[1], identity
    elf = ELFFile(io.BytesIO(STOCK.read_bytes()))
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
    dynamic = list(elf.get_section_by_name('.dynsym').iter_symbols())
    symbols = {s.name: s for s in dynamic}
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)

    def instructions(name):
        sym = symbols[name]
        address = sym['st_value']
        assert sym['st_shndx'] != 'SHN_UNDEF' and not address & 1
        segment = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD'
                       and s['p_vaddr'] <= address < s['p_vaddr'] + s['p_filesz'])
        offset = segment['p_offset'] + address - segment['p_vaddr']
        elf.stream.seek(offset)
        return list(decoder.disasm(elf.stream.read(sym['st_size']), address))

    plt = elf.get_section_by_name('.plt')['sh_addr']
    relocations = list(elf.get_section_by_name('.rel.plt').iter_relocations())
    entries = {dynamic[r['r_info_sym']].name: plt + 20 + i*12 for i, r in enumerate(relocations)}
    split = instructions(SPLIT)
    start = symbols[SPLIT]['st_value']
    checks = []
    for relative, target in ((0x118, SET), (0x154, FINISH)):
        call = next(i for i in split if i.address == start+relative-4)
        assert call.mnemonic == 'bl' and int(call.op_str.lstrip('#'), 16) == entries[target], (target, call.op_str)
        checks.append({'callee': target, 'call': hex(call.address), 'return_relative': hex(relative), 'via_plt': True})
    producer = instructions('_ZN11CRawProcess15frameSubProcessEv')
    still = instructions('_ZN11CFrontEndPP10stillBayerE15eCAP_PATH_VALUE18stFrontPPStillData')
    for body, address, target in ((producer, 0x2ae178, VIRTUAL), (still, 0x1a9028, PP_START)):
        call = next(i for i in body if i.address == address)
        assert call.mnemonic == 'bl' and int(call.op_str.lstrip('#'), 16) == entries[target]
        checks.append({'callee': target, 'call': hex(address), 'return_load_relative': hex(address+4), 'via_plt': True})
    # Actual assignment forwards the output object and returned pool pointer.
    producer_code = {i.address: (i.mnemonic, i.op_str) for i in producer}
    for address, instruction in {0x2ae16c: ('ldr', 'r3, [fp, #-0x20]'),
        0x2ae170: ('ldr', 'r0, [fp, #-0x3c]'), 0x2ae174: ('mov', 'r1, r3')}.items():
        assert producer_code[address] == instruction
    # The operation record is four initialized words; mode 17 and the two
    # image arguments are set immediately before the SetOpParam call.
    expected = {0x18d75c: ('mov', 'r3, #0x11'), 0x18d760: ('str', 'r3, [fp, #-0x38]'),
                0x18d768: ('str', 'r3, [fp, #-0x34]'), 0x18d778: ('str', 'r3, [fp, #-0x2c]')}
    actual = {i.address: (i.mnemonic, i.op_str) for i in split}
    for address, instruction in expected.items():
        assert actual[address] == instruction, (hex(address), actual[address])
    getters = {'_ZN9CCapImage9getGWidthEv': 'ldr r3, [r3]',
               '_ZN9CCapImage10getGHeightEv': 'ldr r3, [r3, #4]',
               '_ZN9CCapImage8getWidthEv': 'ldr r3, [r3, #8]',
               '_ZN9CCapImage9getHeightEv': 'ldr r3, [r3, #0xc]',
               '_ZN9CCapImage10getOffsetXEv': 'ldr r3, [r3, #0x10]',
               '_ZN9CCapImage10getOffsetYEv': 'ldr r3, [r3, #0x14]',
               '_ZN9CCapImage14getImageFormatEv': 'ldr r3, [r3, #0x20]',
               '_ZN9CCapImage13getRawPackBitEv': 'ldr r3, [r3, #0x28]',
               '_ZN9CCapImage12getRawStrideEv': 'ldr r3, [r3, #0x38]',
               '_ZN9CCapImage9getBufferEv': 'add r3, r3, #0x58',
               '_ZN10CCapBuffer10getAddressEPNS_9uCAP_ADDRE': 'add r3, r3, #4',
               '_ZN10CCapBuffer14getVirtAddressEPNS_9uCAP_ADDRE': 'add r3, r3, #0x14'}
    for name, instruction in getters.items():
        assert instruction in [f'{i.mnemonic} {i.op_str}' for i in instructions(name)], name
    offset_calculator = instructions('_ZN9CCapImage15calcImageOffsetEPvN10CCapBuffer14eCAP_ADDR_TYPEEPS_PNS1_9uCAP_ADDREPj')
    plane_size_instructions = {i.address: (i.mnemonic, i.op_str) for i in offset_calculator}
    for address, expected_instruction in {
        0x17215c: ('ldr', 'r2, [fp, #-0x10]'),
        0x172160: ('mul', 'r3, r2, r3'),
        0x172164: ('lsr', 'r3, r3, #2'),
        0x172168: ('str', 'r3, [fp, #-0x18]'),
    }.items():
        assert plane_size_instructions[address] == expected_instruction
    udd = ELFFile(io.BytesIO(STOCK.with_name('libudd.so.3.1').read_bytes()))
    reader_symbol = next(s for s in udd.get_section_by_name('.dynsym').iter_symbols()
                         if s.name == 'read_pp_core_register')
    reader_section = udd.get_section(reader_symbol['st_shndx'])
    reader_offset = reader_symbol['st_value']-reader_section['sh_addr']
    reader = list(decoder.disasm(reader_section.data()[reader_offset:reader_offset+reader_symbol['st_size']],
                                reader_symbol['st_value']))
    reader_code = {i.address:(i.mnemonic,i.op_str) for i in reader}
    # The public reader adds the caller offset directly to the PP base; it
    # does not insert the 0x4000 correction-submodule offset.
    for address, instruction in {0x27590:('ldr','r3, [fp, #-8]'),
        0x27594:('add','r3, r2, r3'),0x27598:('ldr','r3, [r3]')}.items():
        assert reader_code[address] == instruction
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    OUT.mkdir(parents=True, exist_ok=True)
    output = OUT / 'startup_trace_geometry.so'
    command = [compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
               '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
               '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
               '-Wl,-z,nocombreloc', '-DNX300M_CAPTURE_GEOMETRY_TRACE=1', 'runtime/startup_trace.c', '-o', str(output)]
    subprocess.run(command, cwd=PROJECT, check=True)
    with output.open('rb') as stream:
        built = ELFFile(stream)
        exports = {s.name for s in built.get_section_by_name('.dynsym').iter_symbols() if s['st_shndx'] != 'SHN_UNDEF'}
        assert {SET, FINISH, VIRTUAL, PP_START} <= exports
    report = {'stock_elf': str(STOCK), 'split': {'symbol': SPLIT, 'address': hex(start)},
              'hook_edges': checks, 'getter_instructions_verified': getters, 'build_command': command,
              'diagnostic_revision': 4, 'caller_gate': 'verified stock ELF aliases plus exact load-relative return address',
              'pp_register_reader': 'read_pp_core_register 0x27590..0x27598: PP bank base + caller byte offset',
              'stock_alias_realpaths': dict(zip(stock_paths, identity)),
              'memory_policy': 'readable RAM; writable executable stack accepted; read-only executable and device register mappings excluded',
              'secc_plane_stride': 'RawStride / 2; calcImageOffset reserves RawStride * GHeight / 4 bytes per slot',
              'output': str(output), 'enable_environment': {'NX300M_CAPTURE_GEOMETRY': '1'},
              'deployed': False, 'live_capture_observed': False,
              'production_startup_trace_overwritten': False}
    (OUT / 'capture-geometry-trace-build.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (OUT / 'capture-geometry-trace-abi.asm').write_text('\n'.join(
        f'{i.address:08x} {i.mnemonic:8s} {i.op_str}' for body in
        (split, producer, still, instructions(VIRTUAL), instructions('cf_pp_capture_set_rdma_info'), reader)
        for i in body)+'\n', encoding='utf-8')
    print(output)
    print('Stock internal PLT call sites, operation record, getter ABI and hook exports verified; no deployment.')


if __name__ == '__main__':
    main()
