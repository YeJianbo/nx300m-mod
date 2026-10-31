"""Read the last stock-configured resize output and decode its real YCC bytes.

The output is a debug buffer readback, not the camera's LCD or an ISP render.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from pipeline_state import read_state, stock_layout
from session_stock import GUEST, PROJECT


def read_buffer(layout):
    state = read_state(layout)
    info = state['ipcm_combi_resize']['images'][1]
    width, height = info['width'], info['height']
    if not 0 < width <= 8192 or not 0 < height <= 8192 or (width|height)&1:
        raise RuntimeError('No valid stock resize output configured')
    if info['scan_type'] != 1 or info['nlc'] or info['ycc_type'] not in (0, 1):
        raise RuntimeError('Output is not progressive uncompressed YUV420/422')
    root = Path('/proc')/str(state['namespace_pid'])/'root'/GUEST.lstrip('/')
    payload = bytearray()
    with (root/'tmp/nx-sma').open('rb') as memory:
        for chroma in (False, True):
            stride = info['stride_c' if chroma else 'stride_y']
            divisor = 2 if chroma and info['ycc_type'] == 0 else 1
            address = info['c0' if chroma else 'y0']+info['offset_y']//divisor*stride+info['offset_x']
            if stride < width: raise RuntimeError('Invalid plane stride')
            for row in range(height//divisor):
                offset = address+row*stride-0xc6800000
                if offset < 0 or offset+width > 0x18b00000: raise RuntimeError('Output address is outside simulator SMA')
                memory.seek(offset)
                data = memory.read(width)
                if len(data) != width: raise RuntimeError('Short frame read')
                payload.extend(data)
    target = PROJECT/'outputs/firmware-runtime/ipcm-resize-readback.yuv'
    target.write_bytes(payload)
    status_path = root/'var/run/nx300m-ipcm-resize-status.json'
    resize_status = json.loads(status_path.read_text()) if status_path.exists() else None
    return {'namespace_pid': state['namespace_pid'], 'camera_pid': state['camera_pid'], 'configuration': info,
            'width': width, 'height': height, 'bytes': len(payload), 'resize_status': resize_status,
            'process_memory_written': False, 'source': 'stock-configured CPU YCC resize output buffer',
            'display_image_verified': False, 'hardware_bit_exact': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', help=argparse.SUPPRESS)
    args = parser.parse_args()
    path = PROJECT/'outputs/firmware-runtime/ipcm-resize-readback.json'
    if os.name != 'nt':
        result = read_buffer(json.loads(args.layout))
        path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        return
    script = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]+'/tools/inspect_ipcm_frame.py'
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', script, '--layout', json.dumps(stock_layout())],
                            capture_output=True, timeout=20)
    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    from PIL import Image
    report = json.loads(path.read_text())
    width, height = report['width'], report['height']
    data = path.with_suffix('.yuv').read_bytes()
    y = Image.frombytes('L', (width, height), data[:width*height])
    chroma = data[width*height:]
    ch = height//(2 if report['configuration']['ycc_type'] == 0 else 1)
    first = Image.frombytes('L', (width//2, ch), chroma[0::2]).resize((width, height), Image.Resampling.NEAREST)
    second = Image.frombytes('L', (width//2, ch), chroma[1::2]).resize((width, height), Image.Resampling.NEAREST)
    cb, cr = (second, first) if report['configuration']['reverse_cbcr'] else (first, second)
    Image.merge('YCbCr', (y, cb, cr)).convert('RGB').save(path.with_suffix('.png'))
    report['decoded_png'] = str(path.with_suffix('.png'))
    report['decode_assumption'] = 'BT.601 full range, CbCr unless reverse_cbcr; hardware color encoding unconfirmed'
    report['luma_range'] = y.getextrema()
    path.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
