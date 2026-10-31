"""Verify an original firmware ZIP against Samsung's fetched ZIP header, then extract it."""

import argparse
import hashlib
import json
import struct
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    evidence = PROJECT / "work/downloads/samsung-range.bin"
    if not evidence.exists():
        evidence.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl.exe", "--fail", "--location", "--range", "0-4095", "--connect-timeout", "15", "--max-time", "30", "--silent", "--show-error", "--output", str(evidence), "https://org.downloadcenter.samsung.com/content/FM/201412/20141215163358663/NX300M_FW_v1.15.zip"], check=True)
    prefix = evidence.read_bytes()
    header = struct.unpack_from("<4s5H3L2H", prefix)
    if header[0] != b"PK\x03\x04" or header[2] & 8:
        raise RuntimeError("Official ZIP header cannot provide an entry CRC")
    name = prefix[30:30 + header[9]].decode("utf-8")
    if name != "nx300m.bin": raise RuntimeError("Unexpected official ZIP entry")
    expected_crc, compressed, size = header[6:9]
    with args.archive.open("rb") as stream:
        if stream.read(len(prefix)) != prefix: raise RuntimeError("Archive prefix differs from Samsung's response")
    output = PROJECT / "work/stock-1.15"
    output.mkdir(parents=True, exist_ok=True)
    target = output / "nx300m.bin"
    temporary = target.with_suffix(".partial")
    digest = hashlib.sha256()
    with zipfile.ZipFile(args.archive) as archive:
        entry = archive.getinfo(name)
        if (entry.CRC, entry.compress_size, entry.file_size) != (expected_crc, compressed, size):
            raise RuntimeError("Archive metadata differs from Samsung's response")
        with archive.open(entry) as source, temporary.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
                digest.update(chunk)
    if temporary.stat().st_size != size: raise RuntimeError("Extracted size mismatch")
    with temporary.open("rb") as stream: slp = stream.read(64)
    version = slp[4:12].split(b"\0", 1)[0].decode("ascii")
    model = slp[12:28].split(b"\0", 1)[0].decode("ascii")
    if slp[:4] != b"SLP\0" or model != "NX300M" or version != "1.15":
        raise RuntimeError("Not stock NX300M 1.15")
    temporary.replace(target)
    with args.archive.open("rb") as stream:
        zip_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "support_page":"https://www.samsung.com/hk/support/model/EV-NX300MDSTHK/",
        "official_url":"https://org.downloadcenter.samsung.com/content/FM/201412/20141215163358663/NX300M_FW_v1.15.zip",
        "archive":str(args.archive.resolve()), "model":model, "version":version,
        "official_zip_prefix_matches":True, "official_entry_crc32":f"{expected_crc:08x}",
        "zip_entry_crc_verified_on_complete_read":True, "binary_size":size,
        "local_archive_sha256":zip_digest, "local_binary_sha256":digest.hexdigest(),
        "verification_scope":"Samsung ZIP prefix and entry metadata matched; full entry CRC verified. SHA256 identifies local artifacts, not a Samsung-published digest.",
        "prepared_at":datetime.now(timezone.utc).isoformat(),
    }
    (output / "source.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__": main()
