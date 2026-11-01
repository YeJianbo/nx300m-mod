"""Create a fixed camera-photo pan/zoom comparison using quantized IPCM profiles.

This is an offline RGB-code proxy demonstration, not native sensor/ISP footage.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import struct
import subprocess
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cube_to_d4 import load_cube
from fit_ipcm_preview import (ROOT, PROFILES, apply_profile,
                              error_metrics, read_profile, sample_cube)
from look_library import LookLibrary
from look_preview import srgb_image

EVALUATION = ROOT/'outputs/deployment/ipcm-preview/evaluation'
DEFAULT_SOURCES = [ROOT/'outputs/quickshare/photo-send-20261021T034223Z/SAM_4262.JPG',
                   ROOT/'scratch/lut-verify/SAM_5283.JPG']
DEFAULT_LOOKS = ['ricoh-negative-exact', 'ricoh-positive-exact', 'nx300-hard-mono',
                 'nx300-fuji-classic-chrome', 'nx300-fuji-astia', 'nx300-fuji-eterna',
                 'nx300-fuji-classic-negative', 'nx300-fuji-nostalgic-neg']
CORRECTION_LOOKS = ('ricoh-positive-exact', 'nx300-fuji-classic-chrome', 'nx300-fuji-eterna')


def profile_description(path, model):
    """Record the actual payload, including the distinct v1/v2 proxy bases."""
    data = path.read_bytes()
    version, size = struct.unpack_from('<II', data, 4)
    metadata_path = path.with_name('profile.json')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    full = 'ycc_q12' in model
    return {'version': version, 'bytes': len(data), 'declared_bytes': size,
            'matrix_model': 'CCM 3x3 + full YCC 3x3' if full else 'CCM 3x3 + chroma 2x2',
            'quantization': 'signed Q12 matrices; shared monotone 256-entry uint8 tone',
            'rgb_ycc_proxy_basis': ('stock default Q8 matrix [[77,150,29],[-43,-85,128],[128,-107,-21]] / 256'
                                    if full else 'Rec709 full-range zero-centred chroma'),
            'proxy_model_version': metadata.get('proxy_model_version', 'v2-full-ycc-proxy' if full else 'v1-chroma-proxy'),
            'proxy_order': metadata.get('proxy_order', 'CCM -> clip -> shared tone -> RGB/YCC proxy -> post matrix -> RGB -> clip'),
            'model_domain': metadata.get('model_domain', 'RGB code values; native colour encoding, stage order and channel order uncalibrated'),
            'native_model_verified': False}


def transcode_browser_video(path):
    """Use an existing ffmpeg, without installing a package or fetching media."""
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            return {'codec': 'MPEG-4 Part 2 / mp4v', 'browser_transcode': 'existing ffmpeg unavailable'}
    temporary = path.with_name(path.stem+'-h264.mp4')
    result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(path),
                             '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                             '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(temporary)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'ffmpeg exited {result.returncode}: {result.stderr.strip()}')
    temporary.replace(path)
    return {'codec': 'H.264 / avc1', 'pixel_format': 'yuv420p', 'faststart': True,
            'transcode': 'existing ffmpeg libx264 CRF18 medium; no audio', 'ffmpeg': str(ffmpeg)}


def write_index(report, output):
    rows = []
    for look in report['looks']:
        cells = []
        for scene in look['scenes']:
            values = scene['strengths']['100']['all_pixels']
            cells.extend([f'{values["mean_absolute_rgb_8bit"]:.3f}', f'{values["p95_absolute_rgb_8bit"]:.3f}'])
        fmt = look['profile_format']
        rows.append('<tr><th>'+html.escape(look['label'])+'</th><td>'+f'v{fmt["version"]} · {fmt["bytes"]} B'+'</td>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
    scene_head = ''.join(f'<th colspan="2">{html.escape(Path(s["path"]).name)}</th>' for s in report['sources'])
    metric_head = '<th>mean</th><th>P95</th>'*len(report['sources'])
    correction_rows = []
    for look in report['looks']:
        if not look.get('baseline_profile'):
            continue
        scene = look['scenes'][0]
        for amount in ('50', '100'):
            old = scene['baseline_strengths'][amount]['all_pixels']
            new = scene['strengths'][amount]['all_pixels']
            cells = [amount+'%', f'{old["mean_absolute_rgb_8bit"]:.3f}', f'{new["mean_absolute_rgb_8bit"]:.3f}',
                     f'{old["p95_absolute_rgb_8bit"]:.3f}', f'{new["p95_absolute_rgb_8bit"]:.3f}']
            correction_rows.append('<tr><th>'+html.escape(look['label'])+'</th>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
    correction = ''
    if correction_rows:
        correction = '<div class="scroll"><table><caption>同街景修正前后 · 目标始终为同一源 LUT</caption><thead><tr><th>风格</th><th>强度</th><th>旧 v1 mean</th><th>修正版 mean</th><th>旧 v1 P95</th><th>修正版 P95</th></tr></thead><tbody>'+''.join(correction_rows)+'</tbody></table></div><details><summary>展开目标／旧 v1／修正版对照</summary></details>'
    improvement = '<a href="fit-improvement.json">拟合改进记录</a>' if (output/'fit-improvement.json').exists() else ''
    formats = []
    seen = set()
    for look in report['looks']:
        fmt = look['profile_format']
        if fmt['version'] in seen:
            continue
        seen.add(fmt['version'])
        basis = '原厂默认 Q8 RGB/YCC 矩阵与完整 3×3 后矩阵' if fmt['version'] == 2 else 'Rec.709 RGB/YCC 代理与 2×2 色度矩阵'
        formats.append(f'v{fmt["version"]}（{fmt["bytes"]} B）：{basis}')
    page = '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NX300M · IPCM 离线近似对照</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#171918;color:#e8e8e2;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1480px;margin:auto;padding:40px 24px 64px}h1{font-size:30px;letter-spacing:.02em;font-weight:600;margin:0 0 12px}p{color:#b7bcb7;max-width:1000px;margin:10px 0 22px}video{width:100%;display:block;background:#080908;border-radius:6px}a{color:#c8dfac;text-underline-offset:4px}table{border-collapse:collapse;width:100%;max-width:1000px;font-variant-numeric:tabular-nums;margin:24px 0}th,td{padding:12px 16px;border-bottom:1px solid #343a34;text-align:right}th:first-child{text-align:left}thead{color:#b7c3b5}tbody th{font-weight:500}caption{text-align:left;color:#b7bcb7;margin-bottom:10px}details{margin-top:30px}summary{cursor:pointer;color:#c8dfac}img{display:block;width:100%;margin-top:20px}nav{display:flex;gap:24px;margin:24px 0}.scroll{overflow:auto}
</style><main>
<h1>NX300M · IPCM 离线近似对照</h1>
<p>两张本地 NX300M 实拍照片生成固定平移／缩放片段。视频三栏依次为原样、源 LUT 目标、量化 IPCM 近似；每款、每个场景依次展示 0%、50%、100% 强度。</p>
<video controls preload="metadata" src="ipcm-look-comparison.mp4"></video>
<p>这是 RGB 代码域的离线代理模型。硬件颜色编码、通道次序、处理顺序和实际像素效果尚未校准。下表统计编码前的全图误差，单位为 8-bit RGB 码值；全部 0% 误差为 0。</p>
<p>FORMATS。各版本均使用 Q12 矩阵与 8-bit 共享曲线，原厂默认矩阵并不等于已经校准真实硬件成像。</p>
CORRECTION
<div class="scroll"><table><caption>100% 强度 · 每像素 RGB 绝对误差均值及其第 95 百分位</caption><thead><tr><th rowspan="2">风格</th><th rowspan="2">Profile</th>SCENE_HEAD</tr><tr>METRIC_HEAD</tr></thead><tbody>ROWS</tbody></table></div>
<nav><a href="ipcm-look-comparison.mp4" download>下载视频</a><a href="evaluation.json">完整数值 JSON</a><a href="contact-sheet.jpg">三栏对照图</a>IMPROVEMENT</nav>
<details><summary>展开八款 100% 对照图</summary></details>
</main></html>'''
    page = page.replace('SCENE_HEAD', scene_head).replace('METRIC_HEAD', metric_head).replace('ROWS', ''.join(rows))
    page = page.replace('CORRECTION', correction).replace('FORMATS', '；'.join(formats)).replace('IMPROVEMENT', improvement)
    (output/'index.html').write_text(page, encoding='utf-8')


def regions(rgb, target, prediction):
    x, y, p = [a.reshape(-1, 3) for a in (rgb, target, prediction)]
    masks = {'all_pixels': np.ones(len(x), bool),
             'near_gray_code_region': np.ptp(x, axis=1) < .04,
             'blue_code_region': (x[:, 2] > x[:, 0]+.12) & (x[:, 2] > x[:, 1]+.03),
             'skin_like_code_region': (x[:, 0] > x[:, 1]) & (x[:, 1] > x[:, 2]) &
                 (x[:, 0] > .25) & (x[:, 0]-x[:, 2] < .55) & (x[:, 1]-x[:, 2] > .03)}
    return {name: error_metrics(y[mask], p[mask]) if np.any(mask) else {'samples': 0}
            for name, mask in masks.items()}


def crop_pan(image, progress, width, height):
    # A deterministic virtual move within a real still. All panels share it.
    ih, iw = image.shape[:2]
    zoom = 1.0+.13*progress
    crop_w = min(iw, ih*width/height)/zoom
    crop_h = crop_w*height/width
    center_x = iw/2+(progress-.5)*.08*iw
    center_y = ih/2+(progress-.5)*.035*ih
    x0 = int(np.clip(center_x-crop_w/2, 0, iw-crop_w))
    y0 = int(np.clip(center_y-crop_h/2, 0, ih-crop_h))
    return cv2.resize(image[y0:y0+int(crop_h), x0:x0+int(crop_w)], (width, height), interpolation=cv2.INTER_AREA)


def panel_frame(images, label, amount, progress, source_name, width, height, font, labels=None):
    canvas = Image.new('RGB', (width*3, height+76), '#171717')
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 6), f'{label}   {amount:.0%}  |  {source_name}  |  Offline RGB-code proxy; hardware model unverified', fill='white', font=font)
    labels = labels or ['Original / sRGB', 'Source LUT target', 'IPCM Q12 / 8-bit approximation']
    for i, (image, title) in enumerate(zip(images, labels)):
        draw.text((i*width+12, 34), title, fill='#dddddd', font=font)
        canvas.paste(Image.fromarray(crop_pan(image, progress, width, height)), (i*width, 76))
    return np.asarray(canvas)


def evaluate(keys, sources, profiles_root, output, *, fps=15, seconds=1.25, panel_width=480, baseline_profiles_root=None):
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    library = LookLibrary()
    height = panel_width*2//3
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    source_data, evidence = [], []
    for path in sources:
        with Image.open(path) as image:
            exif = image.getexif()
            make, model = exif.get(271, ''), exif.get(272, '')
            if 'SAMSUNG' not in str(make).upper() or str(model).strip() not in ('NX300', 'NX300M'):
                raise ValueError('Expected a real NX300/NX300M image with camera EXIF: '+str(path))
            native_size = image.size
        image, scope, color = srgb_image(path, 1200)
        source_data.append(np.asarray(image).astype(np.float64)/255)
        evidence.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'exif_make': make, 'exif_model': model, 'native_size': native_size,
                         'evaluation_size': image.size, 'source_scope': scope, 'input_color': color})
    video_path = output/'ipcm-look-comparison.mp4'
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (panel_width*3, height+76))
    if not writer.isOpened():
        raise RuntimeError('OpenCV MPEG-4 video writer unavailable')
    report = {'proxy_model_versions': [], 'native_model_verified': False,
              'status': 'offline-quantized-proxy-evaluated', 'sources': evidence,
              'source_kind': 'virtual pan/zoom of local real camera JPEGs; not camera-recorded motion',
              'target': 'exact source .cube trilinear interpolation in RGB code domain, crossfade at 0/50/100 percent',
              'approximation': 'read back final versioned Q12/u8 profiles; actual version, size and RGB/YCC proxy basis recorded per look; parameter strength interpolation matches runtime',
              'presentation': 'model applied before common virtual pan/zoom; metrics computed before crop and video compression',
              'region_note': 'gray/blue/skin-like masks use input RGB code thresholds; not semantic segmentation or measured camera colour regions',
              'native_scope': 'physical pixel equivalence under stock composition, input encoding, channel order and actual IPCM processing order remain unverified',
              'video': {'file': video_path.name, 'codec': 'MPEG-4 Part 2 / mp4v', 'fps': fps,
                        'frame_size': [panel_width*3, height+76], 'frames': 0}, 'looks': []}
    contact_rows, correction_rows = [], []
    if baseline_profiles_root is None:
        baseline_profiles_root = output/'baseline-v1/profiles'
    try:
        for key in keys:
            meta = library.get(key)
            cube = load_cube(library.directory(key)/meta['master'])
            profile_path = profiles_root/key/'profile.bin'
            model = read_profile(profile_path)
            entry = {'id': key, 'label': meta['label'], 'profile': str(profile_path.resolve()),
                     'profile_sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                     'profile_format': profile_description(profile_path, model), 'scenes': []}
            proxy_version = entry['profile_format']['proxy_model_version']
            if proxy_version not in report['proxy_model_versions']:
                report['proxy_model_versions'].append(proxy_version)
            baseline_path = baseline_profiles_root/key/'profile.bin'
            baseline_model = None
            if key in CORRECTION_LOOKS and baseline_path.exists():
                baseline_model = read_profile(baseline_path)
                entry['baseline_profile'] = {'path': str(baseline_path.resolve()),
                                             'sha256': hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                                             'profile_format': profile_description(baseline_path, baseline_model)}
            video_labels = ['Original / sRGB', 'Source LUT target',
                            f'IPCM v{entry["profile_format"]["version"]} / Q12 + u8']
            for source_index, rgb in enumerate(source_data):
                target_full = sample_cube(cube, rgb)
                scene = {'source': sources[source_index].name, 'strengths': {}}
                if baseline_model is not None:
                    scene['baseline_strengths'] = {}
                for amount in (0., .5, 1.):
                    target = rgb+amount*(target_full-rgb)
                    prediction = apply_profile(rgb, model, amount)
                    scene['strengths'][str(round(amount*100))] = regions(rgb, target, prediction)
                    if baseline_model is not None:
                        baseline_prediction = apply_profile(rgb, baseline_model, amount)
                        scene['baseline_strengths'][str(round(amount*100))] = regions(rgb, target, baseline_prediction)
                    if amount == 0 and not np.array_equal(prediction, rgb):
                        raise AssertionError('0 percent profile must be identity')
                    images = [np.floor(np.clip(v, 0, 1)*255+.5).astype(np.uint8) for v in (rgb, target, prediction)]
                    for frame_index in range(max(1, round(seconds*fps))):
                        progress = frame_index/max(1, round(seconds*fps)-1)
                        frame = panel_frame(images, meta['label'], amount, progress,
                                            sources[source_index].name, panel_width, height, font, video_labels)
                        writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                        report['video']['frames'] += 1
                    if source_index == 0 and amount == 1:
                        row = panel_frame(images, meta['label'], amount, .5, sources[source_index].name,
                                          panel_width, height, font, video_labels)
                        contact_rows.append(Image.fromarray(row))
                    if baseline_model is not None and source_index == 0 and amount in (.5, 1.):
                        before_after = [target, baseline_prediction, prediction]
                        comparison_images = [np.floor(np.clip(v, 0, 1)*255+.5).astype(np.uint8) for v in before_after]
                        row = panel_frame(comparison_images, meta['label'], amount, .5, sources[source_index].name,
                                          panel_width, height, font,
                                          ['Source LUT target', 'Previous v1 approximation',
                                           f'Revised v{entry["profile_format"]["version"]} approximation'])
                        correction_rows.append(Image.fromarray(row))
                entry['scenes'].append(scene)
            report['looks'].append(entry)
            print(json.dumps({'id': key, 'scene_100_errors': [scene['strengths']['100']['all_pixels'] for scene in entry['scenes']]}, ensure_ascii=False), flush=True)
    finally:
        writer.release()
    report['video'].update(transcode_browser_video(video_path))
    sheet = Image.new('RGB', (panel_width*3, (height+76)*len(contact_rows)), '#171717')
    for i, row in enumerate(contact_rows):
        sheet.paste(row, (0, i*(height+76)))
    sheet.save(output/'contact-sheet.jpg', quality=93)
    if correction_rows:
        correction_sheet = Image.new('RGB', (panel_width*3, (height+76)*len(correction_rows)), '#171717')
        for i, row in enumerate(correction_rows):
            correction_sheet.paste(row, (0, i*(height+76)))
        correction_sheet.save(output/'correction-comparison.jpg', quality=93)
        report['correction_comparison'] = {'file': 'correction-comparison.jpg', 'looks': [look['id'] for look in report['looks'] if look.get('baseline_profile')],
                                            'source': sources[0].name, 'strengths': [50, 100],
                                            'panels': ['source LUT target', 'previous v1', 'revised profile'],
                                            'baseline_profiles_root': str(baseline_profiles_root.resolve())}
    capture = cv2.VideoCapture(str(video_path))
    decoded = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame.shape[:2] != (height+76, panel_width*3):
            raise RuntimeError('Decoded video dimensions mismatch')
        decoded += 1
    capture.release()
    if decoded != report['video']['frames']:
        raise RuntimeError(f'Video decode produced {decoded} frames, expected {report["video"]["frames"]}')
    report['video']['decoded_frames'] = decoded
    probe = cv2.VideoCapture(str(video_path))
    fourcc = int(probe.get(cv2.CAP_PROP_FOURCC))
    probe.release()
    report['video']['decoded_fourcc'] = ''.join(chr((fourcc >> (i*8)) & 255) for i in range(4))
    report['video']['duration_seconds'] = decoded/fps
    report['elapsed_host_seconds'] = time.monotonic()-started
    report['timing_scope'] = 'host artifact generation only; not camera runtime performance'
    (output/'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write_index(report, output)
    print(json.dumps({'video': str(video_path), 'decoded_frames': decoded, 'report': str(output/'evaluation.json')}, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--look', action='append')
    parser.add_argument('--source', action='append', type=Path)
    parser.add_argument('--profiles-root', type=Path, default=PROFILES)
    parser.add_argument('--output', type=Path, default=EVALUATION)
    parser.add_argument('--baseline-profiles-root', type=Path)
    parser.add_argument('--fps', type=int, default=15)
    parser.add_argument('--seconds', type=float, default=1.25, help='Seconds for each look / scene / strength')
    parser.add_argument('--panel-width', type=int, default=480)
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not .1 <= args.seconds <= 30 or args.panel_width < 160 or args.panel_width % 6:
        parser.error('fps 1..60; seconds .1..30; panel-width >=160 and divisible by 6')
    evaluate(args.look or DEFAULT_LOOKS, args.source or DEFAULT_SOURCES, args.profiles_root, args.output,
             fps=args.fps, seconds=args.seconds, panel_width=args.panel_width, baseline_profiles_root=args.baseline_profiles_root)


if __name__ == '__main__':
    main()
