"""Read the verified stock package and look for its actual embedded kernel config."""

import hashlib
import json
import re
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/quickshare"


def scan_config(data: bytes, label: str) -> dict | None:
    start = data.find(b"IKCFG_ST")
    end = data.find(b"IKCFG_ED", start + 8) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    try:
        config = zlib.decompress(data[start + 8 : end], 31).decode("utf-8")
    except (zlib.error, UnicodeError):
        return None
    if "CONFIG_" not in config:
        return None
    path = OUT / "stock-1.15-kernel.config"
    path.write_text(config, encoding="utf-8")
    names = ("EPOLL", "EVENTFD", "TIMERFD", "FUTEX", "BT", "UNIX", "DEVTMPFS")
    flags = {}
    for name in names:
        match = re.search(rf"^(CONFIG_{name}=.*|# CONFIG_{name} is not set)$", config, re.M)
        flags[name] = match.group(0) if match else "absent"
    return {"source": label, "ikconfig_offset": start, "path": str(path.relative_to(ROOT)), "flags": flags}


def main() -> None:
    source = ROOT / "work/stock-1.15"
    expected = json.loads((source / "source.json").read_text(encoding="utf-8"))
    firmware = source / "nx300m.bin"
    with firmware.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected["local_binary_sha256"]:
        raise RuntimeError("stock package no longer matches its verified artifact identity")
    limit = json.loads((source / "rootfs-manifest.json").read_text())["ubi_offset"]
    with firmware.open("rb") as stream:
        prefix = stream.read(limit)
    report = {"firmware": str(firmware.relative_to(ROOT)), "firmware_identity_verified": True,
              "prefix_bytes_before_rootfs": len(prefix), "raw_zimage_magic_offsets": [],
              "compressed_candidates": [], "embedded_config": scan_config(prefix, "raw package prefix")}
    report["raw_zimage_magic_offsets"] = [m.start() for m in re.finditer(re.escape(b"\x18\x28\x6f\x01"), prefix)]
    for index, match in enumerate(re.finditer(b"\x1f\x8b\x08", prefix)):
        if index >= 128:
            report["candidate_limit_reached"] = True
            break
        offset = match.start()
        try:
            decompressor = zlib.decompressobj(31)
            image = decompressor.decompress(prefix[offset:], 64 * 1024 * 1024)
            if not decompressor.eof:
                continue
        except zlib.error:
            continue
        versions = [m.group().decode("ascii", errors="replace") for m in re.finditer(rb"Linux version [ -~]{1,300}", image)]
        candidate = {"gzip_offset": offset, "decompressed_bytes": len(image), "linux_versions": versions,
                     "ikconfig_start_markers": image.count(b"IKCFG_ST"),
                     "ikconfig_end_markers": image.count(b"IKCFG_ED")}
        if versions:
            path = OUT / f"stock-1.15-kernel-{offset:x}.bin"
            path.write_bytes(image)
            candidate["extracted_path"] = str(path.relative_to(ROOT))
            config = scan_config(image, f"gzip kernel at package offset {offset}")
            if config:
                report["embedded_config"] = config
        report["compressed_candidates"].append(candidate)
    report["conclusion"] = (
        "actual stock kernel embedded config extracted" if report["embedded_config"]
        else "no valid embedded config found in raw package prefix or identified gzip kernel; GPL defconfig is not proof of actual firmware configuration"
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stock-kernel-analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
