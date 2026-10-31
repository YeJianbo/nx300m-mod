"""Check the deployed preview/cache API with the existing local photo copy."""
import io
import json
from pathlib import Path
import urllib.parse
import urllib.request
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8765'


def get(path, headers=None):
    with urllib.request.urlopen(urllib.request.Request(BASE+path, headers=headers or {}), timeout=30) as response:
        return {'status': response.status, 'headers': dict(response.headers)}, response.read()


def main():
    output = ROOT/'outputs/metol/remote'
    evidence = {}
    _, info = get('/api/v1/camera/info')
    assert json.loads(info)['capabilities']['gallery_preview']
    _, listing = get('/cgi-bin/dirlist?p=100PHOTO')
    listing = json.loads(listing)
    evidence['directory'] = listing
    assert {'SAM_4262.JPG', 'SAM_4262.SRW'} <= set(listing['files'])
    fixture = json.loads((output/'media-fixture.json').read_text(encoding='utf-8'))
    evidence['jpeg_download'], jpeg = get('/DCIM/100PHOTO/SAM_4262.JPG')
    assert jpeg == Path(fixture['jpeg']['source']).read_bytes()
    exif = Image.open(io.BytesIO(jpeg)).getexif()
    evidence['jpeg_download'].update(bytes=len(jpeg), exif_tags=len(exif), model=exif.get(272))
    if fixture['jpeg']['kind'] == 'original camera JPEG with EXIF':
        assert exif.get(271) == 'SAMSUNG' and exif.get(272).strip() == 'NX300M'
    path = '/api/v1/gallery/preview?p='+urllib.parse.quote('100PHOTO/SAM_4262.SRW', safe='')
    metadata, thumb = get(path+'&size=grid')
    metadata['bytes'] = len(thumb)
    metadata['dimensions'] = Image.open(io.BytesIO(thumb)).size
    evidence['grid'] = metadata
    assert metadata['dimensions'] == (160, 120) and len(thumb) == 12506
    output.joinpath('gallery-preview.jpg').write_bytes(thumb)
    evidence['grid_repeat'], repeat = get(path)
    assert repeat == thumb and evidence['grid_repeat']['headers']['X-Preview-Cache'] == 'hit'
    evidence['full'], full = get(path+'&size=full')
    evidence['full']['bytes'] = len(full)
    assert full == ROOT.joinpath('outputs/looks/photo-reference.jpg').read_bytes()
    evidence['raw_download_range'], raw_range = get('/DCIM/100PHOTO/SAM_4262.SRW', {'Range': 'bytes=0-63'})
    source = json.loads(ROOT.joinpath('outputs/looks/photo-reference.json').read_text(encoding='utf-8'))
    with Path(source['raw']['path']).open('rb') as stream:
        assert raw_range == stream.read(64)
    assert evidence['raw_download_range']['status'] == 206
    assert listing['sizes']['SAM_4262.SRW'] == Path(source['raw']['path']).stat().st_size
    evidence['original_files_modified'] = False
    evidence['scope'] = 'existing local photo previews and original-file range download; no new camera capture'
    output.joinpath('gallery-session.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'grid_bytes': len(thumb), 'full_bytes': len(full), 'cache': 'hit', 'raw_range': 206}))


if __name__ == '__main__':
    main()
