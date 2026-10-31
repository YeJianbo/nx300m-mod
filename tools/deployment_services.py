"""Statically inventory original firmware service candidates and their ELF interfaces."""
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile

os.environ.setdefault("CONDA_PREFIX", sys.prefix)
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment"
NAMES = re.compile(r"usb|ptp|mtp|sdb|debug|appsvc|fw_upgrade|mss-server|application-service", re.I)
EXACT = {"usr/bin/di-camera-app-nx300", "usr/lib/libcapture-fw-slpcam-nx300.so",
         "usr/bin/production-mode", "usr/lib/libproduction-mode.so", "usr/lib/libproduction-commands.so",
         "usr/lib/libproductivity-internal-mode.so", "usr/lib/libfirmware-upgrade.so", "usr/bin/start_dr.sh"}
INTEREST = re.compile(r"ptp|mtp|vendor|opcode|operation|exec|shell|system|socket|listen|bind|receive|upload|download|sendobject|objectinfo|start|launch|dbus|usb|dev/|tcp|udp|method|interface|service", re.I)


def main():
    files = []
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as archive:
        for member in archive:
            if not NAMES.search(member.name) and member.name not in EXACT:
                continue
            entry = {"path": member.name, "size": member.size, "link": member.linkname}
            if member.isfile():
                data = archive.extractfile(member).read()
                if data.startswith(b"\x7fELF"):
                    elf = ELFFile(io.BytesIO(data))
                    dynamic = elf.get_section_by_name(".dynamic")
                    entry["needed"] = [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == "DT_NEEDED"] if dynamic else []
                    entry["symbols"] = []
                    for name in (".dynsym", ".symtab"):
                        section = elf.get_section_by_name(name)
                        if section:
                            entry["symbols"].extend({"name": s.name, "address": hex(s["st_value"]), "size": s["st_size"], "defined": s["st_shndx"] != "SHN_UNDEF"}
                                                    for s in section.iter_symbols() if INTEREST.search(s.name))
                    strings = re.finditer(rb"[ -~]{5,}", data)
                    entry["strings"] = [{"offset": m.start(), "text": m.group().decode("ascii")[:500]}
                                        for m in strings if INTEREST.search(m.group().decode("ascii"))]
                elif b"\0" not in data and len(data) < 500000:
                    entry["text"] = data.decode("utf-8", "replace")
            files.append(entry)
    (OUT / "stock-service-inventory.json").write_text(json.dumps(files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: v for k, v in f.items() if k in ("path", "size", "link", "needed")} for f in files], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
