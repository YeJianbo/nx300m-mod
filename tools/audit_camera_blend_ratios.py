"""Measure unresolved blend ratios and render coarse/fine visual comparisons."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, identity_lut, parse_lut_file
from render_cxo_ratio_directions import SAMPLES, image_of, save_srgb
from review_camera_palette import OUT, ROOT, scenes


DEST = OUT / 'ratio-audit'
# Cheek patches chosen on the original 720 x 338 portraits, excluding eyes/lips/hair.
SKIN = {
    '暖光人像': [(319/720, 123/338, 347/720, 166/338),
                (422/720, 125/338, 447/720, 165/338)],
    '自然光人像': [(407/720, 65/338, 442/720, 113/338)],
    '冷光人像': [(296/720, 158/338, 341/720, 201/338)],
}


def lab(rgb):
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    xyz = linear @ np.array([[.4124564, .3575761, .1804375],
                             [.2126729, .7151522, .0721750],
                             [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1, 1.08883])
    f = np.where(xyz > (6/29)**3, np.cbrt(xyz), xyz / (3*(6/29)**2) + 4/29)
    return np.stack((116*f[..., 1]-16, 500*(f[..., 0]-f[..., 1]),
                     200*(f[..., 1]-f[..., 2])), axis=-1)


def patch(pixels, rectangles):
    h, w = pixels.shape[:2]
    return np.concatenate([pixels[round(y0*h):round(y1*h), round(x0*w):round(x1*w)].reshape(-1, 3)
                           for x0, y0, x1, y1 in rectangles])


def metrics(source, output, scene):
    original_lab, output_lab = lab(source), lab(output)
    luminance = output_lab[..., 0]
    original_shadow = original_lab[..., 0] < 20
    result = {
        'rgb_rmse_from_original_8bit': float(np.sqrt(np.mean((source-output)**2))*255),
        'any_channel_high_clip_percent': float(np.mean(np.max(output, axis=-1) >= 254.5/255)*100),
        'any_channel_low_clip_percent': float(np.mean(np.min(output, axis=-1) <= .5/255)*100),
        'new_high_clip_percent': float(np.mean((np.max(output, axis=-1) >= 254.5/255)
                                              & (np.max(source, axis=-1) < 254.5/255))*100),
        'new_low_clip_percent': float(np.mean((np.min(output, axis=-1) <= .5/255)
                                             & (np.min(source, axis=-1) > .5/255))*100),
        'luma_L_p01': float(np.percentile(luminance, 1)),
        'luma_L_p10': float(np.percentile(luminance, 10)),
        'luma_L_p90': float(np.percentile(luminance, 90)),
        'luma_L_p99': float(np.percentile(luminance, 99)),
        'shadow_mean_delta_L': float(np.mean((output_lab-original_lab)[..., 0][original_shadow]))
                               if original_shadow.any() else None,
    }
    roi = SKIN.get(scene)
    if roi:
        a, b = patch(original_lab, roi), patch(output_lab, roi)
        mean_a, mean_b = a.mean(axis=0), b.mean(axis=0)
        hue_a, hue_b = np.degrees(np.arctan2(mean_a[2], mean_a[1])), np.degrees(np.arctan2(mean_b[2], mean_b[1]))
        result.update(skin_mean_Lab=mean_b.tolist(),
                      skin_delta_Lab=(mean_b-mean_a).tolist(),
                      skin_hue_shift_deg=float((hue_b-hue_a+180)%360-180),
                      skin_chroma=float(np.hypot(mean_b[1], mean_b[2])),
                      skin_chroma_change=float(np.hypot(mean_b[1], mean_b[2])-np.hypot(mean_a[1], mean_a[2])),
                      skin_delta_E76=float(np.mean(np.linalg.norm(b-a, axis=-1))))
    if scene == '城市街景':
        roi = [(.43, .04, .74, .20)]
        a, b = patch(original_lab, roi).mean(axis=0), patch(output_lab, roi).mean(axis=0)
        result['sky_hue_shift_deg'] = float((np.degrees(np.arctan2(b[2], b[1])-np.arctan2(a[2], a[1]))+180)%360-180)
        result['sky_chroma'] = float(np.hypot(b[1], b[2]))
    return result


def pictures():
    result = scenes()
    image = Image.open(SAMPLES / '32_17_before.jpg').convert('RGB')
    image.thumbnail((420, 260), Image.Resampling.LANCZOS)
    result.insert(2, ('冷光人像', np.asarray(image, dtype=np.float32)/255))
    return result


def draw_grid(entry, photos, a_outputs, b_outputs, amounts, suffix):
    width, height, margin, header, row_height = 278, 192, 24, 108, 244
    canvas = Image.new('RGB', (margin*2 + width*(len(amounts)+1), header+len(photos)*row_height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 27)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    draw.text((margin, 14), entry['a_id'] + '  ×  ' + entry['b_id'], font=title, fill='#f5f1e8')
    draw.text((margin, 57), '比例均指来源 B 的占比；同曝光、白平衡。sRGB 模拟；比例须结合色彩和层次判断。', font=font, fill='#aeb8c2')
    for row, ((scene, original), a, b) in enumerate(zip(photos, a_outputs, b_outputs)):
        top = header + row*row_height
        draw.text((margin, top), scene, font=font, fill='#d9b86a')
        variants = [('原图', original)] + [(f'B {amount}% / A {100-amount}%', a*(1-amount/100)+b*amount/100)
                                            for amount in amounts]
        for column, (label, pixels) in enumerate(variants):
            left = margin + column*width
            canvas.paste(image_of(pixels, (width-4, height)), (left, top+26))
            draw.text((left+5, top+height+28), label, font=font, fill='#f5f1e8')
    path = DEST / (entry['id'] + '-' + suffix + '.jpg')
    save_srgb(canvas, path)
    return path.relative_to(ROOT).as_posix()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fine', help='JSON mapping candidate IDs to displayed integer percentages')
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    entries = json.loads((OUT/'candidates/manifest.json').read_text(encoding='utf-8'))['entries']
    fine = json.loads((ROOT/args.fine).read_text(encoding='utf-8')) if args.fine else None
    photos = pictures()
    rows, records = [], []
    for entry in entries:
        if fine is not None and entry['id'] not in fine:
            continue
        for key in ('a', 'b'):
            if entry[key+'_cube']:
                path = ROOT/entry[key+'_cube']
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry[key+'_sha256']:
                    raise ValueError('Changed source: ' + str(path))
        a = identity_lut() if entry['a_cube'] is None else parse_lut_file(ROOT/entry['a_cube']).lut
        b = parse_lut_file(ROOT/entry['b_cube']).lut
        a_outputs = [apply_lut(source, a) for _, source in photos]
        b_outputs = [apply_lut(source, b) for _, source in photos]
        if fine is None:
            for percent in range(0, 101, 5):
                stats = []
                for (scene, source), left, right in zip(photos, a_outputs, b_outputs):
                    output = left*(1-percent/100) + right*percent/100
                    measure = metrics(source, output, scene)
                    stats.append(dict(scene=scene, **measure))
                    rows.append(dict(id=entry['id'], percent_b=percent, scene=scene,
                                     **{k:v for k,v in measure.items() if not isinstance(v, list)}))
                records.append(dict(id=entry['id'], percent_b=percent, scenes=stats))
        amounts = fine[entry['id']] if fine else [0, 20, 35, 50, 65, 80, 100]
        path = draw_grid(entry, photos, a_outputs, b_outputs, amounts, 'fine' if fine else 'coarse')
        print(path, flush=True)
    if fine is None:
        report = dict(schema=1, source_entries=entries, measured_ratios=list(range(0,101,5)),
                      scenes=[name for name,_ in photos], skin_rectangles_normalized=SKIN,
                      interpretation='No objective aesthetic score. Metrics describe clipping, skin, sky and tone; ratios are selected visually.',
                      camera_runtime_verified=False, records=records)
        (DEST/'measurements.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        fields = list(dict.fromkeys(key for row in rows for key in row))
        with (DEST/'measurements.csv').open('w',encoding='utf-8-sig',newline='') as file:
            writer = csv.DictWriter(file,fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)


if __name__ == '__main__':
    main()
