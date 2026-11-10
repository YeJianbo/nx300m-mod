"""Build a quick visual index of supplied before-image samples."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
OUT = ROOT / 'scratch/cxo-skin-candidates/sample-catalog.jpg'


def main() -> None:
    paths = sorted(SAMPLES.glob('*_before.jpg'))
    columns, thumb_width, thumb_height, label_height, gap, margin = 5, 260, 183, 30, 8, 24
    rows = (len(paths) + columns - 1) // columns
    canvas = Image.new('RGB', (
        columns * thumb_width + (columns - 1) * gap + margin * 2,
        70 + rows * (thumb_height + label_height + gap),
    ), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 24)
    label = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)
    draw.text((margin, 18), '本地人物与生活样片索引', font=title, fill='#f5f1e8')
    for index, path in enumerate(paths):
        column, row = index % columns, index // columns
        left = margin + column * (thumb_width + gap)
        top = 70 + row * (thumb_height + label_height + gap)
        image = Image.open(path).convert('RGB')
        image.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
        x = left + (thumb_width - image.width) // 2
        y = top + (thumb_height - image.height) // 2
        canvas.paste(image, (x, y))
        draw.text((left + 3, top + thumb_height + 6), path.stem.replace('_before', ''), font=label, fill='#c4ccd4')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, quality=92, optimize=True)
    print(OUT.relative_to(ROOT), len(paths))


if __name__ == '__main__':
    main()
