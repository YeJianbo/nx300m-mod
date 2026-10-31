"""Wrap measured NX300M Bayer samples and calibration in a plain DNG.

The DNG is a software-renderer interchange file, not a Samsung firmware image.
No RGB image or JPEG is used as the source of its mosaic.
"""
from fractions import Fraction
from pathlib import Path
import struct

import numpy as np

from adobe_profiles import DcpProfile


def write_bayer_dng(pixels, output, profile, *, whitebalance, black=(0, 0, 0, 0),
                    white=4095, pattern=(1, 0, 2, 1)):
    """Write one uint16 CFA frame with explicit WB, black/white and NX300 DCP.

    Pattern uses DNG plane IDs R=0,G=1,B=2. WB/black order is R,G1,B,G2.
    Pixel coordinates begin at the active visible mosaic, with no fake border.
    """
    pixels = np.asarray(pixels)
    output = Path(output)
    calibration = DcpProfile(profile)
    if calibration.get(50708) != 'Samsung NX300':
        raise ValueError('Expected Samsung NX300 camera calibration')
    if (pixels.dtype != np.uint16 or pixels.ndim != 2 or min(pixels.shape) < 32
            or max(pixels.shape) > 8192 or any(d % 2 for d in pixels.shape)):
        raise ValueError('Expected an even uint16 active Bayer frame, 32..8192 pixels per edge')
    wb = np.asarray(whitebalance, dtype=float)
    black = np.asarray(black, dtype=float)
    if (wb.shape != (4,) or not np.isfinite(wb).all() or np.min(wb) <= 0
            or black.shape != (4,) or not np.isfinite(black).all()
            or np.min(black) < 0 or np.max(black) >= white or not 0 < white <= 65535
            or np.max(pixels) > white or tuple(pattern) not in
            ((0,1,1,2), (1,0,2,1), (1,2,0,1), (2,1,1,0))):
        raise ValueError('Invalid measured WB, levels or Bayer pattern')
    if output.suffix.lower() != '.dng' or output.exists():
        raise ValueError('DNG output must be a new .dng path')
    height, width = pixels.shape
    tags = {}
    def tag(number, kind, values):
        if kind == 2:
            data = values.encode('ascii')+b'\0'; count = len(data)
        elif kind in (5, 10):
            count = len(values); signed = kind == 10
            pairs = [Fraction(float(v)).limit_denominator(1000000) for v in values]
            data = b''.join(struct.pack('<ii' if signed else '<II', v.numerator, v.denominator) for v in pairs)
        else:
            count = len(values)
            data = struct.pack('<'+{1:'B',3:'H',4:'I'}[kind]*count, *values)
        tags[number] = (kind, count, data)
    for number, value in ((256,width), (257,height), (273,0), (278,height), (279,width*height*2)):
        tag(number,4,[value])
    for number, value in ((258,16), (259,1), (262,32803), (274,1), (277,1), (284,1), (50711,1)):
        tag(number,3,[value])
    tag(271,2,'SAMSUNG'); tag(272,2,'NX300M'); tag(305,2,'NX300M measured Bayer DNG interchange')
    tag(33421,3,[2,2]); tag(33422,1,list(pattern))
    tag(50706,1,[1,4,0,0]); tag(50707,1,[1,1,0,0])
    tag(50708,2,'Samsung NX300'); tag(50710,1,[0,1,2])
    tag(50713,3,[2,2])
    green = iter((1,3))
    positions = [next(green) if color == 1 else color for color in pattern]
    tag(50714,5,black[positions]); tag(50717,4,[int(white)])
    tag(50718,5,[1,1]); tag(50719,4,[0,0]); tag(50720,4,[width,height])
    neutral = 1/wb[:3]
    neutral /= neutral[1]
    tag(50728,5,neutral)
    tag(50829,4,[0,0,height,width])
    for number in (50721,50722,50964,50965):
        values = calibration.get(number)
        if values is not None:
            tag(number,10,values)
    for number in (50778,50779):
        values = calibration.get(number)
        if values is not None:
            tag(number,3,[int(values[0])])
    directory_end = 8+2+len(tags)*12+4
    extra = bytearray(); entries = []
    for number, (kind,count,data) in sorted(tags.items()):
        if len(data) <= 4:
            address = data.ljust(4,b'\0')
        else:
            if len(extra) % 2: extra.append(0)
            address = struct.pack('<I',directory_end+len(extra)); extra.extend(data)
        entries.append((number,struct.pack('<HHI',number,kind,count)+address))
    if len(extra) % 2: extra.append(0)
    pixel_offset = directory_end+len(extra)
    entries = [struct.pack('<HHII',273,4,1,pixel_offset) if n == 273 else value for n,value in entries]
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as stream:
        stream.write(b'II*\0'+struct.pack('<I',8))
        stream.write(struct.pack('<H',len(entries)))
        stream.write(b''.join(entries)+b'\0'*4+extra)
        stream.write(pixels.astype('<u2',copy=False).tobytes())
    return {'output':str(output.resolve()),'width':width,'height':height,
            'bayer_pattern':list(pattern),'whitebalance_rg1bg2':wb.tolist(),
            'black_rg1bg2':black.tolist(),'white':white,
            'as_shot_neutral':neutral.tolist(),'calibration_source':str(Path(profile).resolve()),
            'scope':'unmodified measured Bayer + explicit camera calibration DNG interchange'}


def packet_to_dng(packet, reference_raw, output, profile, *, crop):
    """Unpack a PP fixture and verify the active pixels against its original RAW."""
    import rawpy
    from replay_bayer import inspect_packet
    info = inspect_packet(packet)
    x,y,width,height = crop
    if (min(crop) < 0 or min(width,height) == 0 or (x|y|width|height) & 1
            or x+width > info['width'] or y+height > info['height']):
        raise ValueError('Invalid even Bayer crop in packet coordinates')
    packed = np.fromfile(packet,dtype=np.uint8,offset=64).reshape(info['height'],info['stride'])
    groups = packed[:,:info['width']//2*3].reshape(info['height'],-1,3).astype(np.uint16)
    pixels = np.empty((info['height'],info['width']),np.uint16)
    pixels[:,0::2] = groups[:,:,0] | ((groups[:,:,1] & 15) << 8)
    pixels[:,1::2] = (groups[:,:,1] >> 4) | (groups[:,:,2] << 4)
    visible = pixels[y:y+height,x:x+width]
    with rawpy.imread(str(reference_raw)) as raw:
        original = raw.raw_image_visible
        if original.shape != visible.shape or not np.array_equal(original,visible):
            raise ValueError('Packet active pixels do not match the declared original RAW')
        if raw.raw_pattern.tolist() != [[1,0],[2,3]]:
            raise ValueError('Only the measured NX300M GRBG phase is currently supported')
        report = write_bayer_dng(visible,output,profile,whitebalance=raw.camera_whitebalance,
                                 black=raw.black_level_per_channel,white=raw.white_level)
    report.update(packet=str(Path(packet).resolve()),reference_raw=str(Path(reference_raw).resolve()),
                  packet_crop=list(crop),source_pixels_equal=True,matched_pixels=width*height)
    return report
