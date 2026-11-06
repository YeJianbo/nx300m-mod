"""Develop matching SRWs and make photographic RAW/JPEG comparison sheets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import rawpy
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = [(5398, 'CC'), (5399, 'NC'), (5400, 'NN'),
            (5401, '宾得银盐漂白'), (5402, '海盐晴蓝（自制）')]
FONT = Path('C:/Windows/Fonts/msyh.ttc')
BG, FG, SUB = '#10151c', '#f0f4fa', '#b5c0cf'
ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def text(canvas, xy, value, size=28, fill=FG):
    ImageDraw.Draw(canvas).text(xy, value, font=ImageFont.truetype(str(FONT), size), fill=fill)


def save(image, path):
    image.save(path, quality=95, subsampling=0, icc_profile=ICC)


def panel(left, right, title, width=800):
    height = round(width * left.height / left.width)
    canvas = Image.new('RGB', (width*2+72, height+176), BG)
    text(canvas, (24, 16), title, 32)
    text(canvas, (24, 66), 'RAW 无风格显影', 26)
    text(canvas, (width+48, 66), '机内 JPEG · LUT 成片', 26)
    canvas.paste(left.resize((width, height), Image.Resampling.LANCZOS), (24, 110))
    canvas.paste(right.resize((width, height), Image.Resampling.LANCZOS), (width+48, 110))
    text(canvas, (24, height+124), '拍摄白平衡 · 统一 sRGB 显影 · 未做亮度或颜色匹配', 21, SUB)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'outputs/photo-comparison/5398-5402')
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    preserved = ROOT/'pic/raw'
    preserved.mkdir(parents=True, exist_ok=True)
    report = {'renderer': f'rawpy {rawpy.__version__} / LibRaw {rawpy.libraw_version}',
              'settings': {'demosaic': 'AHD', 'use_camera_wb': True,
                           'use_auto_wb': False, 'no_auto_bright': True,
                           'bright': 1.0, 'gamma': [2.4, 12.92],
                           'output_color': 'sRGB', 'output_bps': 16,
                           'highlight_mode': 'Clip', 'user_flip': 0},
              'scope': 'Visual comparison, not a Samsung stock-JPEG or Lightroom render; '
                       'no exposure/color matching, sharpening or LUT applied to RAW reference.',
              'mapping': 'User-specified ascending SRW number order', 'captures': []}
    thumbs = []
    for number, style in CAPTURES:
        stem = f'SAM_{number}'
        source = args.raw_dir/(stem+'.SRW')
        raw_path = preserved/source.name
        if not raw_path.exists():
            shutil.copy2(source, raw_path)
        with Image.open(ROOT/'pic'/(stem+'.JPG')) as source_jpeg:
            exif = source_jpeg.getexif()
            orientation = exif.get(274, 1)
            target_width, target_height = source_jpeg.size
            tags = exif.get_ifd(34665)
            jpeg = ImageOps.exif_transpose(source_jpeg).convert('RGB')
            original_icc = source_jpeg.info.get('icc_profile')
            if original_icc:
                import io
                jpeg = ImageCms.profileToProfile(jpeg,
                    ImageCms.ImageCmsProfile(io.BytesIO(original_icc)),
                    ImageCms.ImageCmsProfile(ICC), outputMode='RGB')
        with rawpy.imread(str(raw_path)) as raw:
            wb = list(raw.camera_whitebalance)
            rgb16 = raw.postprocess(demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                use_camera_wb=True, use_auto_wb=False, no_auto_bright=True,
                bright=1.0, gamma=(2.4, 12.92), output_bps=16,
                output_color=rawpy.ColorSpace.sRGB, user_flip=0,
                highlight_mode=rawpy.HighlightMode.Clip)
        h, w = rgb16.shape[:2]
        if w < target_width or h < target_height:
            raise ValueError(f'{stem}: RAW unexpectedly smaller than JPEG')
        x, y = (w-target_width)//2, (h-target_height)//2
        rgb16 = rgb16[y:y+target_height, x:x+target_width]
        # A single final conversion to 8-bit; no per-image brightness adjustment.
        developed = Image.fromarray(((rgb16.astype(np.uint32)+128)//257).astype(np.uint8))
        transforms = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                      4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                      6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                      8: Image.Transpose.ROTATE_90}
        if orientation in transforms:
            developed = developed.transpose(transforms[orientation])
        assert developed.size == jpeg.size
        developed.save(output/(stem+'-raw-neutral.png'), icc_profile=ICC)
        save(developed, output/(stem+'-raw-neutral.jpg'))
        pair = panel(developed, jpeg, f'{stem}  /  {style}')
        save(pair, output/(stem+'-comparison.jpg'))
        # Identical normalized crops on the two versions of the same exposure.
        regions = [('包装盒 · 红黄与浅灰', (0.13, 0.38, 0.85, 0.79)),
                   ('窗帘与树叶 · 蓝绿', (0.03, 0.03, 0.97, 0.37))]
        crops = []
        for label, box in regions:
            pixel_box = tuple(round(v*developed.size[i%2]) for i, v in enumerate(box))
            crops.append(panel(developed.crop(pixel_box), jpeg.crop(pixel_box),
                               f'{stem}  /  {style}  /  {label}', 800))
        crop_sheet = Image.new('RGB', (crops[0].width, sum(c.height for c in crops)+16), BG)
        cy = 0
        for crop in crops:
            crop_sheet.paste(crop, (0, cy)); cy += crop.height+16
        save(crop_sheet, output/(stem+'-details.jpg'))
        thumbs.append((style, stem, developed.resize((360, 540), Image.Resampling.LANCZOS),
                       jpeg.resize((360, 540), Image.Resampling.LANCZOS)))
        report['captures'].append({'number': number, 'style': style,
            'source_raw': str(raw_path.relative_to(ROOT)),
            'source_jpeg': f'pic/{stem}.JPG', 'camera_whitebalance': wb,
            'raw_developed_unrotated_size': [w, h],
            'center_crop_before_rotation': [x, y, target_width, target_height],
            'orientation': orientation,
            'exposure': {str(k): str(tags[k]) for k in
                [33434, 33437, 34855, 36867, 37386, 41987] if k in tags}})
        print(f'Developed and compared {stem}: {style}', flush=True)
    overview = Image.new('RGB', (1928, 1328), BG)
    text(overview, (24, 16), '五款风格实拍对照  /  RAW 显影与机内 JPEG', 34)
    text(overview, (24, 65), '上排：RAW 无风格显影    下排：机内 LUT 成片    按 SRW 编号升序对应', 24, SUB)
    for i, (style, stem, raw, jpeg) in enumerate(thumbs):
        x = 24+i*380
        text(overview, (x, 110), f'{stem} · {style}', 21)
        overview.paste(raw, (x, 152))
        overview.paste(jpeg, (x, 722))
    text(overview, (24, 1283), '统一 LibRaw / sRGB 显影，非三星原厂直出；未匹配曝光、对比度或白平衡。', 23, SUB)
    save(overview, output/'overview.jpg')
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'README.md').write_text('# 五款实拍 RAW / JPEG 对照\n\n'
        '编号映射：5398 CC；5399 NC；5400 NN；5401 宾得银盐漂白；5402 海盐晴蓝。\n\n'
        'overview.jpg 上排为无风格 RAW 显影，下排为机内 LUT JPEG。每张另附 comparison '
        '全图双栏、details 局部对照及全分辨率 raw-neutral PNG/JPEG。\n\n'
        'RAW 使用 AHD、拍摄白平衡、sRGB 输出、关闭自动提亮，未加入额外风格、曝光匹配或锐化。'
        '中心裁切只为匹配 JPEG 尺寸，不是通过颜色或图像配准拟合。三星的原厂降噪、色调、'
        '镜头校正和 JPEG 成像处理与 LibRaw 不同；这些图不能单独给出 LUT 精度误差，'
        '也不等于 Lightroom/Adobe 原配置文件显影结果。设置及曝光记录见 report.json。\n',
        encoding='utf-8')


if __name__ == '__main__':
    main()
