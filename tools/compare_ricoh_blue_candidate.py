"""Read the two RAW scenes and compare source/Blue Adjusted DCP renders."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from analyze_ricoh_sky_roi import measurement, read_config

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', type=Path,
                        default=PROJECT/'outputs/looks/ricoh-blue-candidate')
    parser.add_argument('--reference-dir', type=Path,
                        default=PROJECT/'outputs/looks/ricoh-reference')
    args = parser.parse_args()
    destination = args.candidate_dir.resolve()
    reference = args.reference_dir.resolve()
    report = {'scope': 'Subjective blue-preserving style comparison; encoded sRGB ROI values, not DeltaE or Ricoh calibration',
              'scenes': []}
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 21)
    roi_normalized = [.83, .23, .96, .28]
    for scene, source_dir in [('blue-sky', reference/'blue-sky'), ('street', reference)]:
        candidate_dir = destination/scene
        original_report = json.loads((source_dir/'report.json').read_text(encoding='utf-8'))
        candidate_report = json.loads((candidate_dir/'report.json').read_text(encoding='utf-8'))
        if original_report['raw'] != candidate_report['raw']:
            raise ValueError('Source and candidate RAW differ')
        scene_report = {'scene': scene, 'raw': candidate_report['raw'], 'comparisons': []}
        sheet = None
        for row, style in enumerate(('positive', 'negative')):
            original_key = style+'-exact'
            candidate_key = style+'-blue-adjusted'
            first_config = read_config(source_dir/(original_key+'.png.pp3'))
            second_config = read_config(candidate_dir/(candidate_key+'.png.pp3'))
            differences = [{'section': section, 'key': name,
                            'source': first_config.get((section, name)),
                            'candidate': second_config.get((section, name))}
                           for section, name in first_config.keys() | second_config.keys()
                           if first_config.get((section, name)) != second_config.get((section, name))]
            if len(differences) != 1 or (differences[0]['section'], differences[0]['key']) != ('Color Management', 'inputprofile'):
                raise ValueError(f'Additional rendering parameter changes: {differences}')
            comparison = {'style': style, 'effective_parameter_differences': differences}
            measurements = []
            for column, (folder, key, title) in enumerate((
                    (source_dir, original_key, style.title()+' Film Exact / source'),
                    (candidate_dir, candidate_key, style.title()+' Film Blue Adjusted / candidate'))):
                with Image.open(folder/(key+'.png')) as image:
                    if sheet is None:
                        size = image.size
                        icc = image.info.get('icc_profile')
                        cell_width = 720
                        cell_height = round(cell_width*size[1]/size[0])
                        sheet = Image.new('RGB', (cell_width*2, (cell_height+45)*2), '#181818')
                    if image.size != size or image.info.get('icc_profile') != icc:
                        raise ValueError('Reference/candidate size or ICC differs')
                    if scene == 'blue-sky':
                        box = [round(roi_normalized[i]*size[i%2]) for i in range(4)]
                        measurements.append(measurement(image.convert('RGB').crop(box)))
                    x, y = column*cell_width, row*(cell_height+45)
                    sheet.paste(image.resize((cell_width, cell_height), Image.Resampling.LANCZOS), (x, y+45))
                    ImageDraw.Draw(sheet).text((x+12, y+10), title, font=font, fill='white')
            if measurements:
                comparison.update(roi_normalized_xyxy=roi_normalized, roi_pixel_xyxy=box,
                                  source_sky=measurements[0], candidate_sky=measurements[1])
                comparison['candidate_minus_source_rgb_8bit'] = [b-a for a, b in zip(
                    measurements[0]['mean_rgb_8bit'], measurements[1]['mean_rgb_8bit'])]
                comparison['candidate_minus_source_hue_degrees_wrapped'] = ((
                    measurements[1]['hsv_of_mean_rgb']['hue_degrees']-
                    measurements[0]['hsv_of_mean_rgb']['hue_degrees']+180)%360)-180
            scene_report['comparisons'].append(comparison)
        sheet.save(candidate_dir/'source-candidate-comparison.jpg', quality=95,
                   subsampling=0, icc_profile=icc)
        report['scenes'].append(scene_report)
    report['only_rendering_parameter_difference_is_dcp_path'] = True
    report['passed'] = True
    (destination/'comparison-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
