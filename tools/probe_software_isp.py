"""Inspect the actual software ISP equations after a stock compression failure.

Runs the same C pixel core natively using observed stock CCM/Gamma/YCC and
factory LUT data. This is a model diagnostic, not an original capture proof.
"""
import argparse
import io
import json
from pathlib import Path
import shlex
import struct
import subprocess

import numpy as np
from PIL import Image
from stock_lut import factory_3d_lut

ROOT=Path(__file__).resolve().parents[1]


def linux(path):
    path=path.resolve()
    return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observer',type=Path,default=ROOT/'outputs/firmware-runtime/capture-software-isp/observer')
    parser.add_argument('--source',type=Path,default=ROOT/'outputs/firmware-runtime/software-isp/source.bin')
    parser.add_argument('--packet',type=Path,default=ROOT/'outputs/firmware-runtime/pp-active-bayer-fixture.bin')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs/firmware-runtime/software-isp/model-inspection')
    parser.add_argument('--width',type=int,default=640)
    parser.add_argument('--height',type=int,default=428)
    args=parser.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    records={}
    for path in args.observer.glob('*.json'):
        data=json.loads(path.read_text(encoding='utf-8'))
        if data['kind'] not in records or records[data['kind']]['sequence']<data['sequence']:
            records[data['kind']]=data
    ccm=records['ccm']['values_int32_bit_view'];ycc=records['rgb2ycc']['values_int32_bit_view'];gamma=records['gamma']['values_uint16']
    params=out/'parameters.bin'
    params.write_bytes(struct.pack('<18i',*ccm,*ycc)+struct.pack('<8192H',*gamma)+
                       struct.pack('<2190H',*factory_3d_lut())+struct.pack('<3I',7,7,6))
    executable=out/'software-isp-host'
    command='cc -O2 -Wall -Wextra -Werror -std=c99 -Iruntime runtime/software_isp_probe.c -o '+shlex.quote(linux(executable))
    subprocess.run(['wsl.exe','-u','root','-e','sh','-c','cd '+shlex.quote(linux(ROOT))+' && '+command],check=True)
    nv16=out/f'model-{args.width}x{args.height}.nv16'
    subprocess.run(['wsl.exe','-u','root','-e',linux(executable),linux(args.packet),linux(args.source),
                    linux(params),linux(nv16),str(args.width),str(args.height)],check=True)
    planes=np.frombuffer(nv16.read_bytes(),dtype=np.uint8).reshape(2,args.height,args.width)
    rgb=Image.fromarray(np.stack([planes[0],np.repeat(planes[1,:,::2],2,axis=1),np.repeat(planes[1,:,1::2],2,axis=1)],axis=2),'YCbCr').convert('RGB')
    image_path=nv16.with_suffix('.png');rgb.save(image_path)
    tables=json.loads((ROOT/'outputs/firmware-runtime/jpeg-encode/quant-tables.json').read_text())['tables'][0]['natural_order_tables']
    encoded=io.BytesIO();rgb.save(encoded,format='JPEG',qtables=tables,subsampling=1)
    pixels=np.asarray(rgb)
    report={'pixel_core':'runtime/software_isp.h native build','width':args.width,'height':args.height,
            'ccm':ccm,'rgb2ycc':ycc,'lut_source':'factory table (baseline model diagnostic)',
            'rgb_mean':pixels.mean(axis=(0,1)).tolist(),'rgb_min':pixels.min(axis=(0,1)).tolist(),
            'rgb_max':pixels.max(axis=(0,1)).tolist(),'host_pillow_jpeg_bytes':len(encoded.getvalue()),
            'stock_task_completion_proven':False,'hardware_bit_exact':False}
    image_path.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
