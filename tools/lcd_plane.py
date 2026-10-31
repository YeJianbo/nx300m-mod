"""Inspect original CDisplayLCD video submissions and decode software scanout.

This is the separate video plane, before composition with the X11 OSD.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from session_stock import GUEST, PROJECT, inspect_session

OUTPUT = PROJECT/'outputs/firmware-runtime'
VIDEO_FIELDS = ('window', 'on', 'format', 'bit', 'address_mode', 'stride',
                'width', 'height', 'x', 'display_width', 'y', 'display_height', 'y0', 'y1', 'c0', 'c1')


def configuration(data):
    return dict(zip(VIDEO_FIELDS, struct.unpack('<5IH2x10I', data)))


def snapshot():
    state = inspect_session()
    if not state['running']: raise RuntimeError('Original camera is not running')
    root = Path('/proc')/str(state['namespace_pid'])/'root'/GUEST.lstrip('/')/'var/run'
    path = root/'nx300m-lcd-state.bin'
    report = {'namespace_pid': state['namespace_pid'], 'camera_pid': state['camera_pids'][0],
              'source': 'original CDisplayLCD ioctls and software video scanout',
              'sensor_emulated': False, 'isp_image_verified': False, 'osd_composed': False,
              'constructed': path.exists(), 'windows': []}
    if not path.exists(): return report, {}
    for _ in range(8):
        data = path.read_bytes()
        if len(data) != 4096: raise ValueError('Invalid LCD state length')
        magic, version, sequence, _ = struct.unpack_from('<4I', data)
        if magic != 0x434c584e or version != 1: raise ValueError('Unsupported LCD state')
        if sequence&1:
            time.sleep(.002)
            continue
        frames = {}
        windows = []
        for index in range(4):
            offset = 16+index*92
            config = configuration(data[offset:offset+64])
            fields = dict(zip(('configured', 'visible', 'ring_size', 'irq', 'submitted', 'completed', 'failed'),
                              struct.unpack_from('<7I', data, offset+64)))
            windows.append({'index': index, 'configuration': config, **fields})
            frame_path = root/f'nx300m-lcd-video-{index}.yuv'
            if fields['completed'] and frame_path.exists(): frames[index] = frame_path.read_bytes()
        if path.read_bytes()[:16] == data[:16]:
            report.update(sequence=sequence, windows=windows)
            return report, frames
    raise RuntimeError('LCD submission is in progress; retry snapshot')


def pack_snapshot(report, frames):
    encoded = json.dumps(report).encode()
    parts = [struct.pack('<I', len(encoded)), encoded]
    for index, data in frames.items(): parts.extend((struct.pack('<II', index, len(data)), data))
    return b''.join(parts)


def unpack_snapshot(data):
    if len(data) < 4: raise ValueError('Missing LCD packet header')
    size = struct.unpack_from('<I', data)[0]
    if size > len(data)-4: raise ValueError('Truncated LCD packet metadata')
    report = json.loads(data[4:4+size])
    frames, offset = {}, 4+size
    while offset < len(data):
        index, size = struct.unpack_from('<II', data, offset)
        offset += 8
        if index > 3 or size > len(data)-offset: raise ValueError('Invalid LCD packet plane')
        frames[index] = data[offset:offset+size]; offset += size
    return report, frames


def decode_frame(data):
    from PIL import Image
    info = configuration(data[:64])
    width, height = info['width'], info['height']
    if not 0 < width <= 8192 or not 0 < height <= 8192 or (width|height)&1 or info['bit'] or info['format'] not in (0, 1):
        raise ValueError('Unsupported LCD video format')
    ch = height//(2 if info['format'] == 0 else 1)
    if len(data) != 64+width*(height+ch): raise ValueError('Invalid LCD frame length')
    y = Image.frombytes('L', (width, height), data[64:64+width*height])
    c = data[64+width*height:]
    cb = Image.frombytes('L', (width//2, ch), c[0::2]).resize((width, height), Image.Resampling.NEAREST)
    cr = Image.frombytes('L', (width//2, ch), c[1::2]).resize((width, height), Image.Resampling.NEAREST)
    return Image.merge('YCbCr', (y, cb, cr)).convert('RGB'), info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name == 'nt':
        script = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]+'/tools/lcd_plane.py'
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', script, '--packet'], capture_output=True, timeout=15)
        if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
        report, frames = unpack_snapshot(result.stdout)
        for index, frame in frames.items():
            target = OUTPUT/f'lcd-video-{index}'
            target.with_suffix('.yuv').write_bytes(frame)
            image, info = decode_frame(frame)
            image.save(target.with_suffix('.png'))
            report['windows'][index]['decoded_png'] = str(target.with_suffix('.png'))
            report['windows'][index]['rgb_extrema'] = list(image.getextrema())
        report['decode_assumption'] = 'BT.601 full range CbCr; hardware color encoding unconfirmed'
        (OUTPUT/'lcd-video.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
        return
    report, frames = snapshot()
    sys.stdout.buffer.write(pack_snapshot(report, frames))


if __name__ == '__main__': main()
