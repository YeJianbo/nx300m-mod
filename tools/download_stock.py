"""Download stock NX300M firmware from Samsung using verified HTTP byte ranges."""

import concurrent.futures
import argparse
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
URL = "https://org.downloadcenter.samsung.com/content/FM/201412/20141215163358663/NX300M_FW_v1.15.zip"
SIZE = 245016220
ETAG = '"012e5433a18d01:0"'
CHUNK = 4 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=URL)
    parser.add_argument('--size', type=int, default=SIZE)
    parser.add_argument('--etag', default=ETAG)
    parser.add_argument('--support-page', default='https://www.samsung.com/hk/support/model/EV-NX300MDSTHK/')
    parser.add_argument('--name', default='NX300M_FW_v1.15.samsung.zip')
    args = parser.parse_args()
    if Path(args.name).name != args.name or not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip', args.name):
        parser.error('--name must be a ZIP basename')
    if args.size <= 0: parser.error('--size must be positive')
    if args.name == 'NX300M_FW_v1.15.samsung.zip' and (args.url, args.size, args.etag) != (URL, SIZE, ETAG):
        parser.error('A different firmware source requires a different --name')
    root = PROJECT / "work/downloads"
    parts = root / ('samsung-parts' if args.name == 'NX300M_FW_v1.15.samsung.zip' else args.name + '.parts')
    parts.mkdir(parents=True, exist_ok=True)
    destination = root / args.name
    source = {"url":args.url, "support_page":args.support_page, "size":args.size, "etag":args.etag}
    source_file = parts / "source.json"
    if source_file.exists() and json.loads(source_file.read_text()) != source:
        raise RuntimeError("Existing parts are from a different source")
    source_file.write_text(json.dumps(source, indent=2), encoding="utf-8")

    def fetch(index):
        start = index * CHUNK
        end = min(args.size, start + CHUNK) - 1
        target = parts / f"{index:03d}.bin"
        if target.exists() and target.stat().st_size == end - start + 1:
            return index
        temporary = target.with_suffix(".partial")
        headers = target.with_suffix(".headers")
        command = ["curl.exe", "--fail", "--location", "--range", f"{start}-{end}", "--header", f"If-Range: {args.etag}", "--connect-timeout", "15", "--max-time", "180", "--retry", "2", "--retry-all-errors", "--silent", "--show-error", "--dump-header", str(headers), "--output", str(temporary), args.url]
        subprocess.run(command, check=True, capture_output=True)
        matches = re.findall(r"Content-Range:\s*bytes\s+(\d+)-(\d+)/(\d+)", headers.read_text(), flags=re.I)
        if not matches or tuple(map(int, matches[-1])) != (start, end, args.size):
            raise RuntimeError(f"Range response mismatch: {index}")
        if temporary.stat().st_size != end - start + 1:
            raise RuntimeError(f"Range length mismatch: {index}")
        temporary.replace(target)
        return index

    count = (args.size + CHUNK - 1) // CHUNK
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetch, index) for index in range(count)]
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            index = future.result()
            print(f"Downloaded {done}/{count} ranges (part {index})", flush=True)
    temporary = destination.with_suffix(".assembling")
    with temporary.open("wb") as out:
        for index in range(count):
            with (parts / f"{index:03d}.bin").open("rb") as stream:
                shutil.copyfileobj(stream, out)
    with zipfile.ZipFile(temporary) as archive:
        bad = archive.testzip()
        if bad: raise RuntimeError(f"ZIP CRC failed: {bad}")
        source["entries"] = [{"name":entry.filename, "size":entry.file_size, "crc32":f"{entry.CRC:08x}"} for entry in archive.infolist()]
    temporary.replace(destination)
    source["downloaded_at"] = datetime.now(timezone.utc).isoformat()
    source["zip_crc_verified"] = True
    manifest = root / ('firmware-source.json' if args.name == 'NX300M_FW_v1.15.samsung.zip' else args.name + '.source.json')
    manifest.write_text(json.dumps(source, indent=2), encoding="utf-8")
    print(f"Verified stock archive: {destination}", flush=True)


if __name__ == "__main__": main()
