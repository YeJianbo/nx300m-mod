"""Read NX300M ELF symbols and SDK headers; never modify source firmware."""

import argparse
import json
import re
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.dynamic import DynamicSection
from elftools.elf.sections import SymbolTableSection


PROJECT = Path(__file__).resolve().parents[1]
SYMBOL_PATTERNS = {
    "picture_wizard": re.compile(r"picture.?wizard|pwcolor|pwsaturation|pwsharp|pwcontrast|set.*pw|pw.*set", re.I),
    "lut": re.compile(r"3d_?lut|lut_?3d|ipcs.*lut|ipcm.*lut", re.I),
    "focus": re.compile(r"focus.*(step|move|position|stack)|(?:set|get).*focus", re.I),
    "capture": re.compile(r"capture.*(?:start|stop)|(?:start|stop).*capture|set.*(?:bitrate|framerate)", re.I),
}
HEADER_PATTERNS = {
    "pref/pref_app.h": r"\b(?:wb_\w+|cwb_\w+|effect_\w+)\b",
    "drime4/udd/ipcs_submodule.h": r"3d_lut|gamma|pcse|chm",
    "drime4/udd/ipcs_submodule_type.h": r"3d_lut|IPCS_LUT_[RGB]|lut_size|lut_data",
    "drime4/udd/ipcm_submodule.h": r"lut|gamma|pcse|chm",
    "drime4/udd/ipcm_submodule_type.h": r"3d_lut|IPCM_LUT_[RGB]|lut_size|lut_data",
}


def inspect_elf(path, base):
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return None
        stream.seek(0)
        elf = ELFFile(stream)
        result = {
            "path": path.relative_to(base).as_posix(),
            "machine": elf["e_machine"],
            "elf_class": elf.elfclass,
            "needed": [],
            "symbols": {key: [] for key in SYMBOL_PATTERNS},
        }
        dynamic = elf.get_section_by_name(".dynamic")
        if isinstance(dynamic, DynamicSection):
            result["needed"] = [tag.needed for tag in dynamic.iter_tags() if tag.entry.d_tag == "DT_NEEDED"]
        seen = set()
        for table_name in (".symtab", ".dynsym"):
            table = elf.get_section_by_name(table_name)
            if not isinstance(table, SymbolTableSection):
                continue
            for symbol in table.iter_symbols():
                name = symbol.name
                identity = (name, symbol["st_value"], str(symbol["st_shndx"]))
                if not name or identity in seen:
                    continue
                seen.add(identity)
                for group, pattern in SYMBOL_PATTERNS.items():
                    if pattern.search(name):
                        result["symbols"][group].append({
                            "name": name,
                            "address": hex(symbol["st_value"]),
                            "size": symbol["st_size"],
                            "defined": symbol["st_shndx"] != "SHN_UNDEF",
                        })
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT / "project.local.json")
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs/firmware-inventory.json")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    rootfs = PROJECT / config["rootfs"]
    sdk = PROJECT / config["sdk"]
    for required in (rootfs / "usr/bin/di-camera-app-nx300",):
        if not required.is_file():
            parser.error(f"Required input missing: {required}")

    report = {
        "rootfs": str(rootfs),
        "sdk": str(sdk),
        "scope": "Static symbols and header declarations, without execution or hardware verification. SDK addresses are not firmware patch addresses.",
        "runtime": [],
        "sdk_applications": [],
        "headers": {},
        "errors": [],
    }
    paths = [rootfs / "usr/bin/di-camera-app-nx300"]
    for directory in (rootfs / "usr/lib", rootfs / "lib"):
        paths.extend(sorted(p for p in directory.glob("*.so*") if p.is_file() and not p.is_symlink()))
    scanned = 0
    for path in paths:
        try:
            entry = inspect_elf(path, rootfs)
            if entry:
                scanned += 1
                if path.name == "di-camera-app-nx300" or any(entry["symbols"].values()):
                    report["runtime"].append(entry)
        except Exception as error:
            report["errors"].append({"path": str(path), "error": str(error)})
    report["runtime_elf_count"] = scanned
    for relative in ("usr/bin/di-camera-app-nx300", "usr/lib/debug/usr/bin/di-camera-app-nx300"):
        path = sdk / relative
        if path.is_file():
            report["sdk_applications"].append(inspect_elf(path, sdk))
    for relative, expression in HEADER_PATTERNS.items():
        path = sdk / "usr/include" / relative
        if not path.is_file():
            report["headers"][relative] = {"status": "SDK not downloaded"}
            continue
        pattern = re.compile(expression, re.I)
        report["headers"][relative] = [
            {"line": line_number, "text": line.strip()}
            for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if pattern.search(line)
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Runtime ELFs scanned: {scanned}; errors: {len(report['errors'])}")
    for group in SYMBOL_PATTERNS:
        runtime = [(entry["path"], sym["name"]) for entry in report["runtime"] for sym in entry["symbols"][group] if sym["defined"]]
        sdk_count = sum(len(entry["symbols"][group]) for entry in report["sdk_applications"] if entry)
        print(f"{group}: runtime definitions={len(runtime)}; SDK symbols={sdk_count}")
        for path, name in runtime[:8]:
            print(f"  {path}: {name}")
    for error in report["errors"]:
        print(f"ERROR: {error['path']}: {error['error']}")
    print(f"Report: {args.output}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
