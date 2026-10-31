"""Export stock NX300M JPEG decode ABI evidence without executing firmware.

Run with the project Conda Python. The JSON contains reviewed findings; the ASM
is regenerated from the supplied ELF files, with direct branch/PLT annotations.
"""
import argparse
import io
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]
SIF_NAMES = [
    '_ZN7DscJfif9JpegParseER7sJpegOp',
    '_ZN7DscJfif10JpegDecodeER7sJpegOp',
    '_ZN10DscJfifHal5ParseEmRmS0_R11eImgYCCTypemm',
    '_ZN10DscJfifHal6DecodeEmmmmmmmmm15eImgYCCPackTypeRmS1_R11eImgYCCTypem',
    '_ZN8DscImage10JpegDecodeE15eEXIFDisplayImg',
    '_ZN8DscImage15JpegDecoderOnlyEv',
    '_ZN8DscImage9DecodeYCCEmRmS0_S0_S0_S0_mS0_S0_S0_R11eImgYCCType15eImgYCCPackTypei',
    '_ZN8DscImage9ParseJfifEmRmS0_R11eImgYCCTypei',
]
UDD_NAMES = ['jpeg_parsingJpegHeader', 'jpeg_parsing_Jpeg_Header',
             'jpeg_decoder', 'jpeg_do_decoder']
JPEG_NAMES = ['jpeg_CreateDecompress', 'jpeg_mem_src', 'jpeg_read_header', 'jpeg_start_decompress',
              'jpeg_read_raw_data', 'jpeg_read_scanlines',
              'jpeg_finish_decompress', 'jpeg_core_output_dimensions',
              'jpeg_calc_output_dimensions']


def inspect(path, names, ranges=()):
    data = path.read_bytes()
    elf = ELFFile(io.BytesIO(data))
    symbols = {}
    for section_name in ('.dynsym', '.symtab'):
        section = elf.get_section_by_name(section_name)
        if section is not None:
            for symbol in section.iter_symbols():
                if symbol['st_shndx'] != 'SHN_UNDEF':
                    symbols.setdefault(symbol.name, symbol)
    targets = {s['st_value'] & ~1: name for name, s in symbols.items()}
    dynamic = elf.get_section_by_name('.dynsym')
    plt = elf.get_section_by_name('.plt')
    relplt = elf.get_section_by_name('.rel.plt')
    if dynamic is not None and plt is not None and relplt is not None:
        for index, relocation in enumerate(relplt.iter_relocations()):
            targets[plt['sh_addr'] + 20 + index * 12] = (
                dynamic.get_symbol(relocation['r_info_sym']).name + '@plt')
    selections = []
    for name in names:
        symbol = symbols[name]
        if symbol['st_info']['type'] != 'STT_FUNC' or not symbol['st_size']:
            raise ValueError(f'{name}: expected a sized function')
        selections.append((name, symbol['st_value'], symbol['st_size']))
    selections.extend(ranges)
    metadata, lines = [], ['# ' + str(path)]
    for name, value, size in selections:
        address = value & ~1
        segment = next(seg for seg in elf.iter_segments()
                       if seg['p_type'] == 'PT_LOAD'
                       and seg['p_vaddr'] <= address
                       and address + size <= seg['p_vaddr'] + seg['p_filesz'])
        offset = segment['p_offset'] + address - segment['p_vaddr']
        raw = data[offset:offset + size]
        decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB if value & 1 else CS_MODE_ARM)
        decoder.skipdata = True
        metadata.append({'name': name, 'address': hex(address),
                         'size_bytes': size, 'file_offset': hex(offset)})
        lines.append(f'\n## {name} {address:#x} size={size}')
        for ins in decoder.disasm(raw, address):
            annotation = ''
            if ins.mnemonic in ('bl', 'blx', 'b') and ins.op_str.startswith('#0x'):
                target = targets.get(int(ins.op_str[1:], 16))
                if target:
                    annotation = ' ; ' + target
            lines.append(f'{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}{annotation}')
    return {'path': str(path), 'size_bytes': len(data), 'functions': metadata}, '\n'.join(lines)


def findings():
    fields = [
        (0x00, 'width', 'SOF width; output reference'),
        (0x04, 'height', 'SOF height; output reference'),
        (0x08, 'y_even', 'output group 1 even virtual address'),
        (0x0c, 'y_odd', 'output group 1 odd virtual address; zero selects derived addressing'),
        (0x10, 'chroma2_even', 'output group 2 even virtual address'),
        (0x14, 'chroma2_odd', 'output group 2 odd virtual address'),
        (0x18, 'chroma3_even', 'output group 3 even virtual address; unused for pack 0'),
        (0x1c, 'chroma3_odd', 'output group 3 odd virtual address; unused for pack 0'),
        (0x20, 'delta_y', 'row delta in derived mode; same-parity row delta in split mode'),
        (0x24, 'delta_c', 'chroma counterpart of delta_y'),
        (0x28, 'jpeg_input', 'readable input JPEG virtual address'),
        (0x2c, 'ycc', 'output enum: 0=422, 1=420'),
        (0x30, 'size_or_aux', 'not consumed by JpegDecode; not a proven compressed length'),
        (0x34, 'pack', '0=two output groups, 1=three output groups'),
        (0x38, 'quality_encode_only', 'not consumed by JpegDecode'),
        (0x3c, 'timeout', 'passed to HAL Decode'),
    ]
    return {
        'scope': 'Static original-ELF inspection only; no camera/session/runtime mutation.',
        'address_convention': 'All instruction addresses are ELF virtual addresses, not relocated process addresses.',
        'task': {'name': 'sJpegOp', 'size_bytes': 64, 'word_bytes': 4,
                 'fields': [{'offset': hex(o), 'name': n, 'meaning': m} for o, n, m in fields],
                 'evidence': 'libsif 0x28998-0x28a50 argument marshalling'},
        'return_and_execution': {
            'entry': 'bool DscJfif::JpegDecode(void *self, sJpegOp &task), libsif 0x2894c',
            'callback': 'self+8; absent callback returns false',
            'success': 'Callback low byte must equal 1: EOR #1, UXTB, compare zero at 0x28a54-0x28a70; hook should return exactly 0 or 1.',
            'hal': 'bma_open -> jpeg_open -> jpeg_init -> jpeg_parsingJpegHeader -> jpeg_decoder -> jpeg_deInit -> jpeg_close',
            'open_failure': 'jpeg_open returns negative: HAL returns false at 0x29570-0x295b0, before parsing/decode.',
            'completion': 'HAL checks request+0x48 at 0x297bc; zero returns 1, nonzero returns 0.',
            'upper_path': 'DscImage::JpegDecoderOnly -> JpegDecode(eEXIFDisplayImg) -> DecodeYCC -> queued sJpegOp; DecodeYCC waits on original semaphore at 0x5c948.',
            'integration': 'Intercept DscJfif::JpegDecode to bypass open failure. Keep upper task/semaphore lifecycle; do not invent hardware success or call completion manually.',
        },
        'addresses_and_length': {
            'input_is_virtual': 'HAL dereferences JPEG first bytes directly (0x296a0-0x296b0); UDD converts input via 0xd3b2c -> bma_virt_to_phys.',
            'outputs_are_virtual': 'UDD output DMA setup 0xd2d28-0xd2f30 converts every provided output base through 0xd3b2c.',
            'no_decode_length': 'No compressed length argument is marshalled by JpegDecode. Task+0x30 is absent from that function.',
            'parse_aux_not_length': 'JpegParse passes +0x30 (0x28900); HAL Parse uses that argument only as a debug-print argument at 0x291c0. SetData bound is width*height (0x290f4-0x29118), with default 1920*1080. Timeout/10 controls marker-loop iterations.',
            'producer': 'ParseJfif 0x5cb38-0x5cb5c writes +0x28 and +0x3c, not +0x30. DecodeYCC likewise writes no +0x30.',
            'required_input_bound': 'Get remaining readable bytes from the actual mapping/allocation; parse JPEG segment lengths and entropy markers within it to EOI. Do not stop at an EOI byte pair embedded inside a length-delimited APP/COM segment.',
            'file_length_boundary': 'DscImage::JpegDecode calls file GetSize/Read, but no explicit length reaches DecodeYCC/JpegDecode.',
        },
        'sampling_and_scale': {
            'sif_enum': {'0': '4:2:2; baseline SOF Y sampling 0x21',
                         '1': '4:2:0; baseline SOF Y sampling 0x22'},
            'udd_enum': {'0': '4:2:0', '1': '4:2:2'},
            'evidence': ['HAL CPU Parse 0x294a8-0x294d8', 'HAL Decode 0x29738-0x29768'],
            'scale': 'No decode scaling parameter: width/height are parsed outputs. Upper DscImage resize is separate.',
            'safe_initial_scope': 'Baseline 8-bit three-component JPEG, sampling 422 or 420 only; other modes need separate evidence.',
            'odd_dimensions': 'Original logical edge/padding behavior is not proved. A software implementation may explicitly reject odd dimensions; do not infer original ceil/padding behavior from register strides.',
        },
        'output_layout': {
            'pack0': 'HAL maps pack 0 to request+4=1: Y plus one interleaved chroma group; third group unused.',
            'pack1': 'HAL maps pack 1 to request+4=0: three output groups. DecodeYCC populates group 3 only when pack==1 (0x5c6a8-0x5c6dc).',
            'pack_other': 'HAL treats every nonzero as three groups, but upper construction only establishes pack 1; reject other values.',
            'mode_selection': 'request+8=(task.y_odd==0), HAL 0x29680-0x2969c; same mode applies to all groups.',
            'derived_mode': 'For each used group: hardware even=virt_to_phys(base), odd=virt_to_phys(base)+delta, row-pair stride=2*delta (0xd2d28-0xd2e48). CPU progressive-row model: base+row*delta.',
            'split_mode': 'Hardware even/odd use separately supplied bases, row-pair stride=delta (0xd2e50-0xd2f44). CPU even/odd-row model: bases[row&1]+(row/2)*delta.',
            'logical_dimensions_even_only': 'Y: width x height. 422 chroma: width/2 x height per planar group; 420: width/2 x height/2. Interleaved chroma row has width bytes.',
            'channel_order_status': 'UNRESOLVED: neither second/third planar group identity (Cb vs Cr) nor pack0 byte order is proved by named register documentation or a native pixel probe.',
            'channel_order_evidence': 'Only group-to-register mapping is proved: Y 0x814/818, group2 0x820/824, group3 0x828/82c; strides 0x81c and 0x830.',
            'software_reference_limit': 'libmmutil_jpeg internal 0xdb4 uses JCS_YCbCr (0x1314) and writes scanline component2 to planar2, component3 to planar3 (0x11dc-0x11e8); it does not establish the SIF JPEG hardware group ordering.',
        },
        'udd_request': {
            'size_bytes_at_least': 76,
            'fields': {'0x00': 'YCC enum 0=420,1=422', '0x04': 'two-group flag',
                       '0x08': 'derived output-address flag', '0x10': 'parsed width',
                       '0x14': 'parsed height', '0x18': 'cleared by header parser',
                       '0x1c': 'Y even', '0x20': 'Y odd', '0x24': 'group2 even',
                       '0x28': 'group2 odd', '0x2c': 'group3 even', '0x30': 'group3 odd',
                       '0x34': 'Y delta', '0x38': 'chroma delta',
                       '0x3c': 'JPEG input virtual address', '0x40': 'timeout', '0x48': 'error'},
            'header': '0xd201c: completion ioctl 0xc0086a07; 0x40c high16=width, low16=height; 0x410&15:2->420,1->422,otherwise -16; missing done -> -5.',
            'decoder': '0xd33a0: MCU count, 0xd268c address checks, 0xd2cac output DMA setup, ioctl 0xc0086a07; missing done -> -5; 0xd31f4 status check; writes +0x48.',
        },
        'software_decoder': {
            'library': 'usr/lib/libjpeg.so.8.3.0 (JPEG_LIB_VERSION 80)',
            'headers': 'work/reference/jpeg8-headers (IJG8d) are NOT directly decompress-ABI compatible: 488 bytes versus stock 504 bytes.',
            'raw_approach_boundary': 'raw_data_out=TRUE + jpeg_read_raw_data can preserve source sampling only after matching the full stock ABI; do not merely add tail padding to bypass its size check.',
            'stock_struct_evidence': {
                'create': '0xec70 compares size with 0x1f8; 0xecb0 clears 504 bytes; 0xed28-0xed34 explicitly clears four unknown extension words at +0x38,+0x3c,+0x40,+0x44.',
                'known_offsets': {'0x2c': 'out_color_space', '0x30': 'scale_num',
                                  '0x34': 'scale_denom', '0x50': 'buffered_image',
                                  '0x54': 'raw_data_out', '0x5c': 'do_fancy_upsampling',
                                  '0x80': 'output_width', '0x84': 'output_height',
                                  '0x9c': 'output_scanline', '0xe8': 'comp_info',
                                  '0x14c': 'max_v_samp_factor',
                                  '0x154': 'min_DCT_v_scaled_size'},
                'component_info': 'stride 0x58; DCT_h/v_scaled_size at +0x24/+0x28; downsampled_width/height at +0x2c/+0x30, jpeg_core_output_dimensions and jpeg_calc_output_dimensions.',
                'unknown_extension': 'The four extra words lie between scale_denom and output_gamma relative to IJG8d. Their semantic names are not established by this inspection.',
            },
            'mmutil_public_api': {
                'symbol': 'mm_util_decode_from_jpeg_memory (ELF 0x302c, size 468)',
                'prototype_arm32': 'int mm_util_decode_from_jpeg_memory(struct result *out, const unsigned char *jpeg, int jpeg_length, int format);',
                'arguments': 'r0=out, r1=input virtual pointer, r2=length, r3=format, forwarded unchanged at 0x309c-0x30ac to internal 0xdb4.',
                'result_size_bytes': 20,
                'result_fields': {'0x00': 'int format', '0x04': 'unsigned width',
                                  '0x08': 'unsigned height', '0x0c': 'unsigned byte_size',
                                  '0x10': 'unsigned char *data (ARM32)'},
                'rgb24': 'format 3: JCS_RGB=2 set at 0x134c; jpeg_start_decompress; width/height stored at 0x107c/0x1084; byte_size=height*row_stride at 0x1080/0x1088; scanlines copied directly at 0x1394-0x13b8.',
                'ycbcr_modes': 'formats 0..2 set JCS_YCbCr=3 at 0x1314, then convert upsampled scanlines into planes at 0x1188-0x1228. This is not raw source-sampling output and cannot replace a sampling-preserving decoder.',
                'grayscale': 'format 4 selects JCS_GRAYSCALE=1 at 0x1428; width*height byte size.',
                'returns': {'0': 'success', '0x80000a22': 'wrapper invalid argument',
                            '0x80000a01': 'libjpeg error/longjmp',
                            '0x80000a05': 'buffer allocation returned NULL',
                            '0x80000a23': 'internal unsupported format'},
                'initialization': 'Zero the 20-byte result before calling: wrapper logs its fields even when internal decode failed (0x30bc-0x30e8). Validate a positive bounded input length; wrapper rejects negatives but accepts zero.',
                'allocation': 'data is allocated by g_malloc0 (call at 0x10c4). Caller owns it; no mmutil free export is provided.',
                'release': 'Call g_free from the same stock libglib-2.0.so.0 dependency. g_malloc0 at 0x4ad70 uses allocator-vtable calloc slot +0xc; g_free at 0x4aed0 uses the matching free slot +8. libc free is not an equivalent guarantee for a configured GLib allocator.',
                'oom_behavior': 'Stock g_malloc0 calls fatal g_log and does not normally return after allocation failure (0x4add0-0x4adf4). Limit dimensions and memory before calling; do not rely on the mmutil NULL branch for recoverable OOM.',
                'dependencies': ['libglib-2.0.so.0', 'libdlog.so.0', 'libpthread.so.0',
                                 'libjpeg.so.8', 'libgcc_s.so.1', 'libc.so.6'],
                'integration_boundary': 'Using format3 avoids exposing jpeg_decompress_struct. RGB24->software YCC is a color round trip, not raw JPEG component preservation or native hardware equivalence.',
            },
            'commit': 'Check all mapped output spans, decode into temporary memory, then copy/commit dimensions and ycc only after complete success.',
            'selection': 'Retain original path by default; an explicit software-decode switch selects the adapter.',
            'limits': ['No JPEG-device emulation or fabricated completion.',
                       'Label the mmutil RGB round trip explicitly; it does not preserve raw JPEG component samples.',
                       'Resolve or explicitly select output chroma convention before enabling that layout.'],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path)
    parser.add_argument('--output', type=Path,
                        default=PROJECT / 'outputs/firmware-runtime/jpeg-decode-abi.json')
    args = parser.parse_args()
    root = args.rootfs
    if root is None:
        config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
        root = Path(config['rootfs'])
    specs = [
        ('usr/lib/libsif.so', SIF_NAMES, []),
        ('usr/lib/libudd.so.3.1', UDD_NAMES,
         [('jpeg_output_dma_setup', 0xd2cac, 0x2ac),
          ('jpeg_decode_internal', 0xd33a0, 0x1e0),
          ('jpeg_virt_to_phys_helper', 0xd3b2c, 40),
          ('jpeg_address_checks', 0xd268c, 0x620),
          ('jpeg_status_check', 0xd31f4, 0x1ac)]),
        ('usr/lib/libjpeg.so.8.3.0', JPEG_NAMES,
         [('jpeg_output_start_internal', 0xf200, 0x100)]),
        ('usr/lib/libmmutil_jpeg.so.0.0.0', ['mm_util_decode_from_jpeg_memory'],
         [('mmutil_memory_decode_internal', 0xdb4, 0x800)]),
        ('usr/lib/libglib-2.0.so.0.2800.6', ['g_malloc0', 'g_free'], []),
    ]
    sources, listings = [], [
        '# Static linear ARM disassembly; data/literal pools can decode as instructions.',
        '# Named ranges use original symbol sizes. Unnamed UDD ranges are reviewed ELF VAs.',
        '# This listing does not execute firmware or establish hardware channel ordering.',
    ]
    for relative, names, ranges in specs:
        metadata, listing = inspect(root / relative, names, ranges)
        metadata['library'] = relative
        sources.append(metadata)
        listings.append(listing)
    report = findings()
    report['sources'] = sources
    asm_path = args.output.with_suffix('.asm')
    report['disassembly'] = str(asm_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asm_path.write_text('\n\n'.join(listings) + '\n', encoding='utf-8')
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(args.output), 'disassembly': str(asm_path),
                      'libraries': len(sources),
                      'functions_or_ranges': sum(len(s['functions']) for s in sources),
                      'channel_order': 'unresolved', 'execution': 'static inspection only'},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
