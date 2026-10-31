"""Compose stock video scanout with the original X11 premultiplied ARGB OSD."""

import json
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops
from lcd_plane import unpack_snapshot, decode_frame

directory = Path(__file__).resolve().parents[1] / "outputs/firmware-runtime"
source = directory / "framebuffer/Xvfb_screen0"
last_display_report = {}

def read_frame(*, live=False):
    global last_display_report
    if live:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('input_stock.py')), 'lcd-frame'], capture_output=True, timeout=15, check=True)
        width, height, stride = struct.unpack_from('<III', result.stdout)
        end = 12+stride*height
        osd = Image.frombytes('RGBA', (width, height), result.stdout[12:end], 'raw', 'BGRA', stride)
        report, planes = unpack_snapshot(result.stdout[end:])
        video = Image.new('RGB', (width, height))
        rendered = []
        for index, data in planes.items():
            window = report['windows'][index]
            if not window['visible']: continue
            image, info = decode_frame(data)
            if (info['display_width'], info['display_height']) != image.size:
                raise ValueError('LCD video geometry needs an unimplemented crop mode')
            video.paste(image, (info['x'], info['y']))
            rendered.append(index)
        # Evas/Xvfb preserves alpha in the high byte even for this 24-depth,
        # 32-bpp window. Black RGB can have alpha 0 or 255; never color-key it.
        alpha = osd.getchannel('A')
        for window in report.get('opaque_osd_windows', []):
            x, y = window['x'], window['y']
            alpha.paste(255, (x, y, x+window['width'], y+window['height']))
        background = Image.composite(Image.new('RGB', video.size), video, alpha)
        image = ImageChops.add(osd.convert('RGB'), background)
        last_display_report = {**report, 'osd_composed': bool(rendered), 'rendered_windows': rendered,
                               'osd_alpha_range': alpha.getextrema(), 'osd_alpha_values': len(alpha.getcolors(256) or []),
                               'composition': 'premultiplied-argb-over-original-video-plane',
                               'decode_assumption': 'BT.601 full range CbCr; hardware color encoding unconfirmed'}
    else:
        data = source.read_bytes()
        header = struct.unpack_from('>25I', data)
        header_size, _, _, _, width, height, _, byte_order, _, _, _, bits_per_pixel, stride, _, red, green, blue, _, _, colors, *_ = header
        if not (bits_per_pixel == 32 and byte_order == 0 and (red, green, blue) == (0xff0000, 0xff00, 0xff)):
            raise ValueError('Unsupported XWD pixel format')
        image = Image.frombytes('RGB', (width, height), data[header_size + colors * 12:], 'raw', 'BGRX', stride)
    if (width, height) == (480, 800): image = image.transpose(Image.Transpose.ROTATE_90)
    return image, (width, height)

def main():
    image, (width, height) = read_frame(live='--live' in sys.argv)
    image.save(directory / 'camera-xvfb.png')
    report = {'source':'stock X11 OSD and CDisplayLCD video scanout' if '--live' in sys.argv else str(source), 'raw_width':width, 'raw_height':height, 'width':image.width, 'height':image.height, 'rotation_ccw_degrees':90 if (width, height) == (480, 800) else 0, 'extrema':image.getextrema(), 'uniform':all(low == high for low, high in image.getextrema()), 'display':last_display_report}
    (directory / 'framebuffer.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))

if __name__ == '__main__': main()
