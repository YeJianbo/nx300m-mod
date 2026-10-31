"""Preview creative sRGB LUTs on local JPEGs or original SRW embedded JPEGs."""
import io
import math
from pathlib import Path
import struct

from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFont, ImageOps, ImageStat
from PIL.ImageFilter import Color3DLUT as ImageFilterLUT

from cube_to_d4 import load_cube


def srgb_image(source, max_edge):
    source = Path(source)
    if source.suffix.lower() == '.srw':
        from photo_pairs import srw_metadata
        info = srw_metadata(source)
        jpeg = info.get('embedded_jpeg')
        if not jpeg:
            raise ValueError('SRW has no embedded JPEG')
        with source.open('rb') as stream:
            stream.seek(jpeg['offset'])
            content = stream.read(jpeg['bytes'])
        opened = Image.open(io.BytesIO(content))
        scope = 'original SRW embedded JPEG; RAW pixels are not developed'
    else:
        opened = Image.open(source)
        scope = 'existing rendered image'
    with opened:
        image = ImageOps.exif_transpose(opened)
        profile = opened.info.get('icc_profile')
        if profile:
            image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                              ImageCms.createProfile('sRGB'), outputMode='RGB',
                                              renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
            color = 'embedded ICC converted to sRGB, relative colorimetric'
        else:
            image = image.convert('RGB')
            color = 'sRGB assumed; no embedded ICC profile'
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return image.copy(), scope, color


def master_filter(path):
    size, low, high, values = load_cube(path)
    if size > 65:
        raise ValueError('Image preview supports cube grids up to 65; D4 compilation supports up to 129')
    if low != (0., 0., 0.) or high != (1., 1., 1.):
        # An affine input-domain mapping can be applied as a separate LUT.
        domain = ImageFilterLUT.generate(2, lambda r, g, b: tuple((v-a)/(z-a) for v, a, z in zip((r,g,b), low, high)))
    else:
        domain = None
    return domain, ImageFilterLUT(size, [v for rgb in values for v in rgb], channels=3)


def native_filter(path):
    # Pillow uses the .cube axis order (R fastest). D4 stores R outer/B inner.
    data = Path(path).read_bytes()
    if len(data) != 4380:
        raise ValueError('Expected three 730-word D4 planes')
    words = struct.unpack('<2190H', data)
    if any(v > 1023 or (i % 730 == 729 and v != 0) for i, v in enumerate(words)):
        raise ValueError('Invalid D4 LUT data')
    table = [words[c*730+(r*9+g)*9+b]/1023
             for b in range(9) for g in range(9) for r in range(9) for c in range(3)]
    return ImageFilterLUT(9, table, channels=3)


def render_preview(library, key, source, output, *, amount=1., max_edge=900):
    if not math.isfinite(amount) or not 0 <= amount <= 1:
        raise ValueError('Preview amount must be in [0,1]')
    if not 64 <= max_edge <= 4096:
        raise ValueError('Preview max edge must be 64..4096')
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError('Preview must use a separate output file')
    if output.suffix.lower() != '.png':
        raise ValueError('Comparison preview output must be PNG')
    if output.exists():
        raise FileExistsError('Preview output already exists: '+str(output))
    metadata = library.get(key)
    if metadata['input_space'] != 'srgb' or metadata['output_space'] != 'srgb':
        raise ValueError('Photo preview requires an explicitly sRGB-to-sRGB look')
    directory = library.directory(key)
    original, source_scope, input_color = srgb_image(source, max_edge)
    domain, transform = master_filter(directory/metadata['master'])
    sample = original.filter(domain) if domain is not None else original
    master = Image.blend(original, sample.filter(transform), amount)
    native = Image.blend(original, original.filter(native_filter(directory/metadata['native_table'])), amount)
    difference = ImageStat.Stat(ImageChops.difference(master, native))
    # The two native surfaces still have unknown hardware interpolation/color
    # encoding. This comparison isolates grid reduction and 10-bit quantization.
    margin, title, gap = 18, 48, 12
    width, height = original.size
    canvas = Image.new('RGB', (margin*2+width*3+gap*2, title+height+margin), '#181818')
    draw = ImageDraw.Draw(canvas)
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = ImageFont.truetype(str(font_path), 16) if font_path.exists() else ImageFont.load_default()
    labels = ['原图 / sRGB', f'{metadata["label"]} / master {amount:.0%}', '9³ / 10 bit 离线近似']
    for index, (image, label) in enumerate(zip((original, master, native), labels)):
        x = margin+index*(width+gap)
        draw.text((x, 12), label, fill='white', font=font)
        canvas.paste(image, (x, title))
    output.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    with output.open('xb') as stream:
        canvas.save(stream, format='PNG', icc_profile=icc)
    return {'id': key, 'source': str(source.resolve()), 'output': str(output.resolve()),
            'source_scope': source_scope, 'input_color': input_color,
            'amount': amount, 'panel_size': original.size,
            'master_vs_d4_mean_absolute_rgb_8bit': difference.mean,
            'master_vs_d4_rms_rgb_8bit': difference.rms,
            'interpolation': 'Pillow trilinear, 8-bit image output',
            'native_preview_table': metadata['native_table'],
            'scope': 'offline creative LUT and reduced-grid comparison, not full Lightroom or camera ISP output',
            'camera_image_verified': False, 'ricoh_color_calibrated': False}
