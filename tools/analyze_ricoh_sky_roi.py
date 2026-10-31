"""Compare one fixed sky ROI before/after the DCP LookTable in RAW references.

Measurements describe encoded sRGB pixels, not DeltaE or Ricoh calibration.
"""
import argparse
import colorsys
import configparser
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

PROJECT = Path(__file__).resolve().parents[1]


def read_config(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding='utf-8')
    return {(section, key): value for section in parser.sections()
            for key, value in parser[section].items()}


def measurement(image):
    stat = ImageStat.Stat(image)
    hue, saturation, value = colorsys.rgb_to_hsv(*(v/255 for v in stat.mean))
    return {'mean_rgb_8bit': stat.mean, 'stddev_rgb_8bit': stat.stddev,
            'hsv_of_mean_rgb': {'hue_degrees': hue*360,
                                'saturation_percent': saturation*100,
                                'value_percent': value*100}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-dir', type=Path,
                        default=PROJECT/'outputs/looks/ricoh-reference/blue-sky')
    parser.add_argument('--no-look-dir', type=Path,
                        default=PROJECT/'outputs/looks/ricoh-reference/blue-sky-no-look')
    args = parser.parse_args()
    full = args.full_dir.resolve()
    no_look = args.no_look_dir.resolve()
    full_report = json.loads((full/'report.json').read_text(encoding='utf-8'))
    no_report = json.loads((no_look/'report.json').read_text(encoding='utf-8'))
    if full_report['raw'] != no_report['raw'] or no_report['apply_look_table']:
        raise ValueError('The ablation must use the same RAW with LookTable disabled')
    normalized = [.83, .23, .96, .28]
    result = {'raw': full_report['raw'], 'roi_normalized_xyxy': normalized,
              'scope': 'same RAW, renderer and fixed open-sky ROI; encoded sRGB RGB/HSV diagnostic, not DeltaE or Ricoh calibration',
              'hsv_method': 'colorsys.rgb_to_hsv(mean RGB / 255); not arithmetic mean of hue',
              'comparisons': []}
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 22)
    crops = []
    for key in ('positive-exact', 'negative-exact'):
        first_config = read_config(full/(key+'.png.pp3'))
        second_config = read_config(no_look/(key+'.png.pp3'))
        differences = [{'section': section, 'key': name,
                        'full': first_config.get((section, name)),
                        'without_look_table': second_config.get((section, name))}
                       for section, name in first_config.keys() | second_config.keys()
                       if first_config.get((section, name)) != second_config.get((section, name))]
        if differences != [{'section': 'Color Management', 'key': 'applylooktable',
                            'full': 'true', 'without_look_table': 'false'}]:
            raise ValueError(f'Unexpected configuration differences: {differences}')
        values = []
        for folder, label in ((full, 'LookTable ON'), (no_look, 'LookTable OFF')):
            with Image.open(folder/(key+'.png')) as image:
                if 'reference_size' not in result:
                    result['reference_size'] = list(image.size)
                    result['roi_pixel_xyxy'] = [round(normalized[i]*image.size[i%2]) for i in range(4)]
                    result['icc_bytes'] = len(image.info.get('icc_profile', b''))
                    icc = image.info.get('icc_profile')
                if list(image.size) != result['reference_size'] or image.info.get('icc_profile') != icc:
                    raise ValueError('Different dimensions or ICC in ablation')
                crop = image.convert('RGB').crop(result['roi_pixel_xyxy'])
                values.append(measurement(crop))
                crops.append((key+' / '+label, crop.copy()))
        delta_rgb = [a-b for a, b in zip(values[0]['mean_rgb_8bit'], values[1]['mean_rgb_8bit'])]
        hue_delta = ((values[0]['hsv_of_mean_rgb']['hue_degrees']-
                      values[1]['hsv_of_mean_rgb']['hue_degrees']+180)%360)-180
        result['comparisons'].append({'profile': key, 'effective_parameter_differences': differences,
                                      'look_table_on': values[0], 'look_table_off': values[1],
                                      'on_minus_off_rgb_8bit': delta_rgb,
                                      'on_minus_off_hue_degrees_wrapped': hue_delta})
    with Image.open(full/'adobe-standard.png') as baseline:
        result['adobe_standard_roi'] = measurement(baseline.convert('RGB').crop(result['roi_pixel_xyxy']))
        preview = baseline.convert('RGB')
        preview.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(preview)
        box = [round(normalized[i]*preview.size[i%2]) for i in range(4)]
        draw.rectangle(box, outline='#ff4010', width=4)
        draw.text((box[0]-240, box[3]+8), 'Fixed open-sky ROI', font=font, fill='#ff4010')
        preview.save(no_look/'sky-roi-location.jpg', quality=95, subsampling=0, icc_profile=icc)
    width, height = crops[0][1].size
    sheet = Image.new('RGB', (width*2, (height+45)*2), '#181818')
    draw = ImageDraw.Draw(sheet)
    for index, (label, crop) in enumerate(crops):
        x, y = (index%2)*width, (index//2)*(height+45)
        sheet.paste(crop, (x, y+45))
        draw.text((x+10, y+10), label, font=font, fill='white')
    sheet.save(no_look/'sky-roi-comparison.jpg', quality=98, subsampling=0, icc_profile=icc)
    result['only_effective_parameter_difference_is_look_table'] = True
    (no_look/'sky-roi-comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
