"""Evaluate the fixed Lightroom import cohort with the existing D4 protocols.

Reuses compiler, metrics and photo rendering; no upstream C rebuild or change
to the installed library tables. Each look is saved before moving to the next.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from cube_to_d4 import load_cube
from evaluate_d4_quality import (SOURCES, byte_output, metrics, region_metrics,
                                 save_png, transitions, write_index)
from import_lightroom_rgb_tables import (CreativeRgbTable, DLL, PROFILES,
                                         decode_payload, extract_tables, fields)
from look_library import LookLibrary
from look_preview import srgb_image
from lut_resample import compile_table, sample_cube, sample_table, target, unpack
from stock_lut import factory_3d_lut


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/looks/lightroom-import'
METHODS = {'fit': 'fit', 'integral': 'fit-integral'}


def summary(rows):
    result = {}
    for metric in ('rgb_rmse', 'oklab_mean_x100', 'rgb_p95', 'hue_mean_degrees',
                   'style_projection_gain'):
        result[metric] = {name: float(np.mean([r['rgb8_holdout'][name]['all'][metric] for r in rows
                                              if r['rgb8_holdout'][name]['all'][metric] is not None]))
                          for name in ('passthrough', 'legacy', *METHODS)}
    result['regressions_vs_legacy'] = {
        name: {metric: [r['id'] for r in rows if r['rgb8_holdout'][name]['all'][metric] >
                        r['rgb8_holdout']['legacy']['all'][metric]+1e-9]
               for metric in ('rgb_rmse', 'oklab_mean_x100')}
        for name in METHODS}
    result['regional_rgb_rmse'] = {
        region: {name: float(np.mean([r['rgb8_holdout'][name][region]['rgb_rmse'] for r in rows]))
                 for name in ('legacy', *METHODS)}
        for region in ('near_gray', 'skin_like', 'green', 'blue', 'shadow', 'highlight')}
    result['photo_rgb_rmse'] = {
        name: float(np.mean([s['backends'][name]['rgb_rmse'] for r in rows for s in r['scenes']]))
        for name in ('legacy', *METHODS)}
    result['source_bake_rgb_rmse'] = float(np.mean([r['source_bake']['all']['rgb_rmse'] for r in rows]))
    return result


def contact_sheets(report, output):
    # One representative per family, plus characteristic colour/duotone looks.
    chosen = ('nikon-dream', 'nikon-denim', 'sigma-powder-blue', 'sigma-teal-and-orange',
              'sigma-duotone-b1', 'lr-artistic-01', 'lr-modern-05', 'lr-vintage-03',
              'nx300-fuji-bleach-bypass')
    rows = [row for key in chosen for row in report['looks'] if row['id'] == key]
    names = (('original', '原图'), ('target', '源 LUT'), ('legacy', '原 9³'), ('fit', '拟合 9³'))
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    for scene in range(len(report['sources'])):
        width, height = 320, 254
        sheet = Image.new('RGB', (width*len(names), height*len(rows)), '#171a1d')
        draw = ImageDraw.Draw(sheet)
        for ri, row in enumerate(rows):
            for col, (key, title) in enumerate(names):
                path = output/'images'/row['id']/f'{scene}-{key}.png'
                with Image.open(path) as opened:
                    im = opened.copy()
                im.thumbnail((width-10, height-38))
                x, y = col*width, ri*height
                draw.text((x+5, y+4), row['label']+' / '+title, font=font, fill='white')
                sheet.paste(im, (x+(width-im.width)//2, y+34))
        sheet.save(output/f'comparison-{scene}.png', icc_profile=profile)


def run(output=OUT, resume=False):
    output = Path(output)
    inventory = json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))
    if not inventory['preexisting_preserved']:
        raise ValueError('Import did not finish its preexisting-library check')
    rng = np.random.default_rng(40719)
    continuous = rng.random((24000, 3))
    colors = np.floor(rng.random((16000, 3))*256).astype(np.uint8)
    gray = np.repeat(np.arange(256, dtype=np.uint8)[:, None], 3, axis=1)
    hold = np.concatenate((colors, gray)).astype(float)/255
    photos, sources = [], []
    for path in SOURCES:
        im, scope, color = srgb_image(path, 480)
        photos.append(np.asarray(im).reshape(-1, 3).astype(float)/255)
        sources.append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'shape': [im.height, im.width, 3], 'scope': scope, 'color': color})
    report = {'schema': 1, 'seed': 40719, 'fit_training_seed': 61183,
              'model': 'RGB code / trilinear; hardware colour domain and interpolation unmeasured',
              'strength': 1., 'photos_used_for_training': False,
              'default_compiler_changed': False, 'camera_runtime_changed': False,
              'cohort': [e['id'] for e in inventory['entries']], 'sources': sources, 'looks': []}
    output.mkdir(parents=True, exist_ok=True)
    saved = {}
    if resume and (output/'report.json').exists():
        old = json.loads((output/'report.json').read_text(encoding='utf-8'))
        if old['sources'] != sources or old['cohort'] != report['cohort']:
            raise ValueError('Resume inputs differ from saved evaluation')
        saved = {row['id']: row for row in old['looks']}
    inputs = np.concatenate((hold, *photos))
    stock_words = factory_3d_lut()
    stock = unpack(struct.pack('<2190H', *stock_words))
    library = LookLibrary()
    attributes_by_id = {e['id']: fields(PROFILES/e['resource']) for e in inventory['entries']}
    missing = {e['rgb_table_id'] for e in inventory['entries']
               if 'Table_'+e['rgb_table_id'] not in attributes_by_id[e['id']]}
    external = extract_tables(DLL, missing) if missing else {}
    for entry in inventory['entries']:
        key = entry['id']
        directory = library.directory(key)
        path = directory/'source.cube'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if key in saved:
            if saved[key]['source_sha256'] != digest:
                raise ValueError('Resume source changed: '+key)
            report['looks'].append(saved[key])
            continue
        cube = load_cube(path)
        row = {'id': key, 'label': entry['label'], 'source_grid': cube[0],
               'source_sha256': digest, 'declared_input_space': 'srgb',
               'continuous_holdout': {}, 'rgb8_holdout': {}, 'gray': {},
               'scenes': [], 'transitions': {}, 'compiler': {}, 'composed': {}}
        destination = output/'profiles'/key
        destination.mkdir(parents=True, exist_ok=True)
        variants = {'legacy': (directory/'table.d4lut').read_bytes()}
        unpack(variants['legacy'])
        for name, method in METHODS.items():
            data, detail = compile_table(cube, method=method)
            (destination/(method+'.d4lut')).write_bytes(data)
            variants[name] = data
            row['compiler'][name] = detail
        continuous_goal = sample_cube(cube, continuous)
        goals = byte_output(sample_cube(cube, inputs))
        predictions = {'passthrough': inputs}
        for name, data in variants.items():
            row['continuous_holdout'][name] = region_metrics(
                continuous, continuous_goal, sample_table(data, continuous))
            row['transitions'][name] = transitions(cube, data)
            predictions[name] = byte_output(sample_table(data, inputs))
        for name, pred in predictions.items():
            row['rgb8_holdout'][name] = region_metrics(hold, goals[:len(hold)], pred[:len(hold)])
            row['gray'][name] = metrics(hold[-256:], goals[len(hold)-256:len(hold)],
                                        pred[len(hold)-256:len(hold)])

        # Measure conversion to the saved .cube separately from reduction to
        # 9^3, so source baking is not misreported as D4 fitting error.
        attributes = attributes_by_id[key]
        fingerprint = entry['rgb_table_id']
        raw = (decode_payload(attributes['Table_'+fingerprint], fingerprint)
               if 'Table_'+fingerprint in attributes else external[fingerprint][0])
        native = CreativeRgbTable(raw)
        row['source_bake'] = region_metrics(continuous, native.apply(continuous), continuous_goal)
        composed_goal = target(cube, continuous, stock)
        old_composed = (directory/'table-stock3d.d4lut').read_bytes()
        row['composed']['legacy'] = region_metrics(continuous, composed_goal, sample_table(old_composed, continuous))
        for name, method in METHODS.items():
            data, detail = compile_table(cube, stock_words, method=method)
            (destination/(method+'-stock3d.d4lut')).write_bytes(data)
            row['composed'][name] = region_metrics(continuous, composed_goal, sample_table(data, continuous))
            row['composed'][name+'_compiler'] = detail

        image_dir = output/'images'/key
        image_dir.mkdir(parents=True, exist_ok=True)
        at = len(hold)
        for i, (photo, source) in enumerate(zip(photos, sources)):
            end = at+len(photo)
            save_png(image_dir/f'{i}-original.png', photo, source['shape'])
            save_png(image_dir/f'{i}-target.png', goals[at:end], source['shape'])
            scene = {'name': source['name'], 'backends': {}}
            for name, pred in predictions.items():
                scene['backends'][name] = metrics(photo, goals[at:end], pred[at:end])
                if name != 'passthrough':
                    save_png(image_dir/f'{i}-{name}.png', pred[at:end], source['shape'])
            row['scenes'].append(scene)
            at = end
        (destination/'analysis.json').write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        report['looks'].append(row)
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'id': key, 'done': len(report['looks']), 'total': len(report['cohort']),
                          'rmse': {name: row['rgb8_holdout'][name]['all']['rgb_rmse']
                                   for name in ('legacy', *METHODS)}}, ensure_ascii=False), flush=True)
    report['summary'] = summary(report['looks'])
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write_index(report, output)
    contact_sheets(report, output)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    report = run(args.output, args.resume)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
