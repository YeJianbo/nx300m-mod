"""Prepare same-RAW GRBG12 calibration for the explicit software ISP model.

Checks every visible mosaic sample against the preserved packet. The output is
white-balance/geometry metadata only; no JPEG, RGB or YCC is rendered here.
"""
import argparse
import json
from pathlib import Path
import struct

import numpy as np
import rawpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--packet', type=Path, default=ROOT/'outputs/firmware-runtime/pp-active-bayer-fixture.bin')
    parser.add_argument('--metadata', type=Path, default=ROOT/'outputs/firmware-runtime/pp-active-bayer-fixture.json')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/firmware-runtime/software-isp/source.bin')
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding='utf-8'))
    data = args.packet.read_bytes()
    header = struct.unpack('<16I', data[:64])
    magic, version, width, height, stride, bits, *reserved = header
    if (magic,version,bits)!=(0x3252584e,1,12) or any(reserved) or len(data)!=64+stride*height:
        raise ValueError('Invalid recorded Bayer packet')
    x,y = metadata['reconstructed_border']['active_offset']
    packed = np.frombuffer(data, dtype=np.uint8, offset=64).reshape(height,stride)
    triplets = packed[:,:width//2*3].astype(np.uint16).reshape(height,width//2,3)
    mosaic = np.empty((height,width),dtype=np.uint16)
    mosaic[:,::2] = triplets[:,:,0] | ((triplets[:,:,1]&15)<<8)
    mosaic[:,1::2] = (triplets[:,:,1]>>4) | (triplets[:,:,2]<<4)
    with rawpy.imread(str(args.raw)) as raw:
        if raw.raw_pattern.tolist()!=[[1,0],[2,3]] or raw.color_desc.rstrip(b'\0')!=b'RGBG':
            raise ValueError('Only the measured GRBG RAW is supported')
        crop_width,crop_height = raw.sizes.width,raw.sizes.height
        measured = raw.raw_image[:crop_height,:crop_width]
        if measured.shape!=(crop_height,crop_width) or not np.array_equal(mosaic[y:y+crop_height,x:x+crop_width],measured):
            raise ValueError('Recorded visible Bayer samples differ from this RAW')
        black = list(raw.black_level_per_channel)
        if any(black):
            raise ValueError('The current 12-bit gamma software model requires the measured zero black levels')
        wb = list(raw.camera_whitebalance)
        if not all(v>0 for v in wb) or raw.white_level!=4095:
            raise ValueError('Missing same-RAW white balance or unexpected white level')
        wb_q12 = [round(v/wb[1]*4096) for v in wb]
        values = [0x4953584e,1,width,height,stride,x,y,crop_width,crop_height,*wb_q12,*black,raw.white_level]
        report = {'source_raw':str(args.raw.resolve()),'packet':str(args.packet.resolve()),
                  'visible_mosaic_matches_same_raw':True,'compared_samples':int(measured.size),
                  'crop':[x,y,crop_width,crop_height],'crop_geometry':'visible RAW scaled to each requested JPEG geometry',
                  'camera_whitebalance':wb,'white_balance_q12':wb_q12,'black_levels':black,
                  'white_level':raw.white_level,'cfa':'GRBG','source_kind':'recorded_real_Bayer',
                  'software_model':'same-CFA separable [1,2,1] lowpass, bilinear CFA, same-RAW WB, final stock Q7 CCM, 13bit gamma, trilinear 3D LUT, Q8 YCC',
                  'hardware_bit_exact':False,'calibrated_srgb_proven':False,
                  'excluded':['PP DPC/CS/DAMP','hardware Bayer interpolation','hardware ISP rounding and enable-state equivalence']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(struct.pack('<18I',*values))
    args.output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
