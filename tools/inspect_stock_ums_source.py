"""Extract the vendor USB gadget sources for offline command-path review."""
import gzip
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/ums-source'


def main():
    origin = json.loads((ROOT / 'work/vendor-source/SOURCE.json').read_text(encoding='utf-8'))
    source = Path(origin['archive'])
    prefix = (ROOT / 'work/downloads/source-range.bin').read_bytes()
    with source.open('rb') as stream:
        if source.stat().st_size != 638285114 or stream.read(len(prefix)) != prefix:
            raise ValueError('Vendor archive differs from the previously verified source')
    report = {'archive': str(source), 'files': [], 'device_accessed': False}
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = re.compile(r'^TIZEN/packages_nx300m/linux/(?:drivers/usb/gadget/[^/]+|include/linux/usb/[^/]+)\.(?:c|h)$')
    with source.open('rb') as compressed, gzip.GzipFile(fileobj=compressed) as stream:
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            for member in archive:
                relative = PurePosixPath(member.name.removeprefix('./'))
                if not member.isfile() or not wanted.match(relative.as_posix()):
                    continue
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Unsafe archive member')
                data = archive.extractfile(member).read()
                target = OUT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                report['files'].append({'path': relative.as_posix(), 'bytes': len(data)})
        # Read through the gzip trailer to verify its CRC, even after tar EOF.
        while stream.read(1024 * 1024):
            pass
    report['gzip_crc_verified'] = True
    (OUT / 'source-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'files': len(report['files']), 'gzip_crc_verified': True, 'device_accessed': False}))


if __name__ == '__main__':
    main()
