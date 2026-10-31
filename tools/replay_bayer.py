"""Prepare original SRW mosaic samples for an explicit PP WDMA recorded input.

No demosaic, white balance, exposure adjustment or color rendering is performed.
The pair packing follows stock dbg_get_pixelValue_fromPackBayer ARM instructions.
"""
import argparse
import json
from pathlib import Path
import struct

MAGIC = 0x3252584e


def inspect_packet(path):
    path = Path(path)
    with path.open('rb') as stream: header = stream.read(64)
    if len(header) != 64: raise ValueError('Short Bayer packet header')
    magic, version, width, height, stride, bits, *reserved = struct.unpack('<16I', header)
    if magic != MAGIC or version != 1 or bits != 12 or any(reserved):
        raise ValueError('Unsupported Bayer packet header')
    if not 0 < width <= 8192 or not 0 < height <= 8192 or (width|height)&1:
        raise ValueError('Bayer packet dimensions must be positive, even, at most 8192')
    if stride < width//2*3 or stride > 32768 or stride&7:
        raise ValueError('Bayer packet stride must fit packed row and be 8-byte aligned')
    if path.stat().st_size != 64+stride*height: raise ValueError('Bayer packet size mismatch')
    return {'width': width, 'height': height, 'stride': stride, 'bits': bits, 'payload_bytes': stride*height}


def prepare(source, target, stride, layout=None):
    import numpy as np
    import rawpy
    from photo_pairs import srw_metadata
    source, target = Path(source), Path(target)
    metadata = srw_metadata(source)
    if metadata.get('model') not in ('NX300', 'NX300M'):
        raise ValueError('Expected a Samsung NX300/NX300M SRW source')
    if target.exists() or target.with_suffix('.json').exists(): raise FileExistsError(target)
    with rawpy.imread(str(source)) as raw:
        pixels = raw.raw_image.copy()
        raw_info = {'raw_width': raw.sizes.raw_width, 'raw_height': raw.sizes.raw_height,
                    'visible_width': raw.sizes.width, 'visible_height': raw.sizes.height,
                    'top_margin': raw.sizes.top_margin, 'left_margin': raw.sizes.left_margin,
                    'pattern': raw.raw_pattern.tolist(), 'color_description': raw.color_desc.decode('ascii'),
                    'black_levels': raw.black_level_per_channel, 'white_level': raw.white_level}
    border = None
    if layout:
        mode = json.loads(Path(layout).read_text(encoding='utf-8-sig'))['sensor_still_mode']
        width, height = mode['global_width'], mode['global_height']
        x, y = mode['offset_x'], mode['offset_y']
        active_width, active_height = mode['effective_width'], mode['effective_height']
        if (min(x, y) < 0 or (x|y)&1 or active_width <= 0 or active_height <= 0 or
                x+active_width > width or y+active_height > height or
                active_width > pixels.shape[1] or active_height > pixels.shape[0] or
                not 0 < width <= 8192 or not 0 < height <= 8192):
            raise ValueError('Stock active window does not fit source or destination')
        frame = np.zeros((height, width), dtype=np.uint16)
        frame[y:y+active_height, x:x+active_width] = pixels[:active_height, :active_width]
        border = {'value': 0, 'measured': False, 'source_crop_origin': [0, 0], 'active_offset': [x, y],
                  'active_size': [active_width, active_height], 'layout_source': str(Path(layout).resolve()),
                  'scope': 'recorded active Bayer samples with explicitly reconstructed constant border'}
        pixels = frame
    height, width = pixels.shape
    if pixels.dtype != np.uint16 or not 0 < width <= 8192 or not 0 < height <= 8192 or (width|height)&1:
        raise ValueError('Unsupported decoded Bayer dimensions/type')
    if int(pixels.max()) > 4095: raise ValueError('Source has samples outside the native 12-bit range')
    row_bytes = width//2*3
    if stride < row_bytes or stride > 32768 or stride&7: raise ValueError('Invalid row stride')
    packed = np.zeros((height, stride), dtype=np.uint8)
    first, second = pixels[:, 0::2], pixels[:, 1::2]
    packed[:, :row_bytes:3] = first&255
    packed[:, 1:row_bytes:3] = (first >> 8) | ((second&15) << 4)
    packed[:, 2:row_bytes:3] = second >> 4
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(struct.pack('<16I', MAGIC, 1, width, height, stride, 12, *([0]*10)))
        stream.write(packed.tobytes())
    report = {'source': str(source.resolve()), 'source_metadata': metadata, 'libraw': raw_info,
              **inspect_packet(target), 'format': 'two pixels in three bytes: low8(a), high4(a)|low4(b)<<4, high8(b)',
              'boundary': 'PP WDMA recorded Bayer output; sensor/D4C/PP pixel processing bypassed by explicit fixture',
              'processing': ('LibRaw decode; recorded active crop placed in stock window with explicit unmeasured zero border'
                             if border else 'LibRaw decode only; full mosaic retained including borders, no pixel scaling or color rendering'),
              'sample_range': [int(pixels.min()), int(pixels.max())], 'rawpy_version': rawpy.__version__,
              'reconstructed_border': border,
              'image_verified': False, 'hardware_bit_exact': False}
    target.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('prepare')
    add.add_argument('source', type=Path); add.add_argument('output', type=Path)
    add.add_argument('--stride', type=int, required=True, help='Explicit stock-configured bytes per row')
    add.add_argument('--layout', type=Path, help='Explicit stock pipeline JSON: position recorded active samples and fill missing border with unmeasured zero')
    check = commands.add_parser('inspect'); check.add_argument('packet', type=Path)
    args = parser.parse_args()
    result = prepare(args.source, args.output, args.stride, args.layout) if args.command == 'prepare' else inspect_packet(args.packet)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
