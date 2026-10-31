"""Build an offline stock-1.15 start-hook candidate and a diagnostic-only SD probe."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tarfile

from elftools.elf.elffile import ELFFile

import build_slp_candidate as slp
import build_stock_start_hook as hook
import deployment_ubifs as ubifs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/minimal-start"
MEMBER = "usr/lib/libmisc.so"
PROBE = b'''#!/bin/sh
# Diagnostic probe: only these SD log/marker files are written.
PATH=/bin:/usr/bin
export PATH
root=/mnt/mmc/nx300m-mod
test -f "$root/DISABLED" && exit 0
test -f "$root/minimal-start.ok" && exit 0
umask 077
{
    printf 'NX300M minimal start probe v1\\n'
    date
    id
    uname -a
    printf 'probe complete\\n'
} > "$root/minimal-start.log" 2>&1 || exit 1
printf 'diagnostic-only\\n' > "$root/minimal-start.ok"
'''


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify_hook(directory, report):
    """Reconstruct the reviewed patch from stock, then check bytes and report."""
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as archive:
        member = archive.getmember(MEMBER)
        original = archive.extractfile(member).read()
    generated_stock = (directory / "libmisc.stock.so").read_bytes()
    patched = (directory / "libmisc.so").read_bytes()
    if generated_stock != original:
        raise ValueError("Retained original differs from verified stock TAR member")
    elf = ELFFile(io.BytesIO(original))
    rx_index, rx = next((i, s) for i, s in enumerate(elf.iter_segments())
                        if s["p_type"] == "PT_LOAD" and s["p_flags"] == 5)
    dynamic = list(elf.get_section_by_name(".dynsym").iter_symbols())
    relocations = list(elf.get_section_by_name(".rel.plt").iter_relocations())
    index = next(i for i, r in enumerate(relocations) if dynamic[r["r_info_sym"]].name == "system")
    system_plt = elf.get_section_by_name(".plt")["sh_addr"] + 20 + 12 * index
    append = (len(original) + 15) & ~15
    stub = struct.pack("<5I", 0xe92d4010, 0xe28f0008,
                       hook.branch(append + 8, system_plt, True), 0xe3a00000, 0xe8bd8010)
    expected = bytearray(original)
    expected.extend(b"\0" * (append - len(original)) + stub + hook.COMMAND)
    phoff = elf["e_phoff"] + rx_index * elf["e_phentsize"]
    fields = [phoff + 16, phoff + 20]
    struct.pack_into("<I", expected, hook.ENTRY, hook.branch(hook.ENTRY, append))
    for offset in fields:
        struct.pack_into("<I", expected, offset, len(expected))
    if patched != expected or original[hook.ENTRY:hook.ENTRY + 8] != hook.EXPECTED_ENTRY:
        raise ValueError("Generated library differs from the reviewed stock patch")
    ranges = []
    for offset, (before, after) in enumerate(zip(original, patched)):
        if before != after:
            if not ranges or ranges[-1][1] != offset:
                ranges.append([offset, offset + 1])
            else:
                ranges[-1][1] += 1
    checked = {
        "original_size": len(original), "patched_size": len(patched),
        "original_member_sha256": hashlib.sha256(original).hexdigest(),
        "patched_member_sha256": hashlib.sha256(patched).hexdigest(),
        "original_entry_hex": original[hook.ENTRY:hook.ENTRY + 8].hex(),
        "patched_entry_hex": patched[hook.ENTRY:hook.ENTRY + 8].hex(),
        "existing_byte_diff_ranges_end_exclusive": ranges,
        "appended_range_end_exclusive": [len(original), len(patched)],
        "RX_length_field_offsets": fields, "stub_address": append,
        "command": hook.COMMAND[:-1].decode(),
    }
    for name, expected_value in checked.items():
        if report.get(name) != expected_value:
            raise ValueError("Start-hook report differs from actual bytes: " + name)
    if hook.elf_symbols(original) != hook.elf_symbols(patched):
        raise ValueError("Dynamic symbols changed")
    hook.check_segments(ELFFile(io.BytesIO(patched)))
    if rx["p_offset"] != 0 or rx["p_vaddr"] != 0:
        raise ValueError("Unexpected stock RX mapping")
    metadata = {"mode": oct(stat.S_IMODE(member.mode)), "uid": member.uid,
                "gid": member.gid, "mtime": member.mtime}
    return patched, metadata, checked


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    hook_directory = OUT / "start-hook"
    previous_out = hook.OUT
    try:
        hook.OUT = hook_directory
        hook_report = hook.build()
    finally:
        hook.OUT = previous_out
    patched, metadata, checked = verify_hook(hook_directory, hook_report)
    overlay = OUT / "overlay/rootfs"
    destination = overlay / MEMBER
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(patched)
    save(overlay.parent / "report.json", {"rootfs_changes": [{
        "destination": "/" + MEMBER, "bytes": len(patched),
        "change": "modified", "metadata": metadata,
    }]})
    script = OUT / "sd/nx300m-mod/start.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(PROBE)
    sd_files = [p.relative_to(OUT / "sd").as_posix() for p in (OUT / "sd").rglob("*") if p.is_file()]
    if sd_files != ["nx300m-mod/start.sh"]:
        raise ValueError("Diagnostic SD staging contains unexpected files; do not merge full services here")
    archive_path = OUT / "nx300m-minimal-start-sd.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("nx300m-mod/start.sh")
        info.mode, info.uid, info.gid, info.mtime = 0o755, 0, 0, 0
        info.size = len(PROBE)
        archive.addfile(info, io.BytesIO(PROBE))
    with tarfile.open(archive_path) as archive:
        if archive.getnames() != ["nx300m-mod/start.sh"] or archive.extractfile(archive.getmembers()[0]).read() != PROBE:
            raise ValueError("SD archive readback differs from diagnostic probe")
    return overlay, checked, archive_path


def build(prepare_only=False):
    overlay, hook_bytes, sd_archive = prepare()
    report = {
        "model": "NX300M", "stock_version": "1.15", "rootfs_overlay_files": [MEMBER],
        "start_hook_byte_verification": hook_bytes, "sd_archive": str(sd_archive),
        "sd_probe": "writes only minimal-start.log and minimal-start.ok; no service or hook loading",
        "status": "prepared", "firmware_package_created": False,
        "camera_accessed": False, "updater_executed": False, "release_ready": False,
        "not_verified": ["kernel mount", "real camera boot and start_script invocation",
                         "upgrader acceptance and recovery", "sleep/wake behavior"],
        "sd_warning": "Use a separate clean staging/card. Do not merge with an existing nx300m-mod directory: the start.sh path is shared with the full mod.",
    }
    save(OUT / "report.json", report)
    if prepare_only:
        return report
    comparison = ubifs.build(overlay, output_dir=OUT / "ubifs", overlay_modes={MEMBER: None})
    differences = comparison.get("expected_stock_differences", [])
    if (not comparison["passed"] or comparison.get("unexpected_difference_count") != 0
            or len(differences) != 1 or differences[0]["path"] != MEMBER
            or differences[0]["change"] != "modified"
            or set(differences[0]["fields"]) != {"size", "sha256"}):
        raise ValueError("Rootfs changes exceed the single reviewed library size/content replacement")
    platform = OUT / "ubifs/platform-mod-candidate.ubi"
    firmware = OUT / "firmware/nx300m-minimal-start-1.15.slp"
    slp_report = slp.build(platform, firmware)
    report.update({"status": "offline_candidate_built", "firmware_package_created": True,
                   "firmware": str(firmware), "firmware_bytes": firmware.stat().st_size,
                   "rootfs_path_count": comparison["path_count"], "rootfs_readback_passed": True,
                   "expected_stock_differences": differences,
                   "ubi_report": str(OUT / "ubifs/ubinize-report.json"),
                   "slp_report": str(firmware.parent / "build-report.json"),
                   "unchanged_partitions": slp_report["unchanged_partitions"],
                   "platform_byte_equal": slp_report["platform_byte_equal"]})
    save(OUT / "report.json", report)
    return report


def check_parser():
    """Check this candidate with the existing read-only original ARM parser."""
    import probe_stock_slp as probe

    firmware = OUT / "firmware/nx300m-minimal-start-1.15.slp"
    previous_out = probe.OUT
    try:
        probe.OUT = OUT / "parser"
        stage, _bad_fixture, metadata = probe.prepare(firmware)
        result = probe.run_case("candidate-parser", firmware, "NX300M", stage)
        if (result["exit_code"] != 0 or not result["crc_helper_reached"]
                or not result["crc_update_reached"]):
            raise ValueError("Original SLP parser did not accept the candidate with CRC enabled")
        if probe.digest(probe.STOCK) != metadata["stock_sha256_verified"]:
            raise ValueError("Original stock input changed during parser validation")
        original_elf = ROOT / "outputs/deployment/stock/usr/sbin/fw_upgrade"
        if probe.digest(original_elf) != metadata["original_elf_sha256"]:
            raise ValueError("Original fw_upgrade ELF changed during parser validation")
        save(probe.OUT / "report.json", {
            "candidate_parser_passed": True, "case": result,
            "stock_input_and_original_elf_unchanged": True,
            "scope": "Original SLP reader and CRC only, in existing read-only isolated ARM harness; no upgrade main or flash writer",
            "controls": "Previously passed stock/model/CRC controls were not rerun for this candidate",
            "camera_accessed": False, "updater_executed": False,
        })
    finally:
        probe.OUT = previous_out
    report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    report.update({"candidate_stock_parser_passed": True,
                   "candidate_parser_report": str(OUT / "parser/report.json")})
    save(OUT / "report.json", report)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Prepare the byte-checked overlay and SD probe without building UBI/SLP")
    parser.add_argument("--check-stock-parser", action="store_true", help="After building, check this candidate with the existing read-only stock ARM parser")
    args = parser.parse_args()
    if args.prepare_only and args.check_stock_parser:
        parser.error("--check-stock-parser requires a complete build")
    report = build(args.prepare_only)
    if args.check_stock_parser:
        check_parser()
    print(json.dumps({k: report[k] for k in ("status", "firmware_package_created", "rootfs_overlay_files", "release_ready")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
