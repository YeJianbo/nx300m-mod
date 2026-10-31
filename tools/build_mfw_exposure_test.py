"""Package the manual MFW read-only exposure startup candidate, offline only."""
from pathlib import Path
import zipfile

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/mfw-exposure-test"


def main():
    module = ROOT / "outputs/controls/exposure_status.so"
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or elf.header.e_machine != "EM_ARM" or elf.header.e_type != "ET_DYN":
            raise ValueError("Expected the ARM read-only shared module")
        symbols = elf.get_section_by_name(".dynsym")
        exports = {s.name for s in symbols.iter_symbols() if s.entry.st_shndx != "SHN_UNDEF"}
        if exports != {"ecore_main_loop_begin", "nx_auto_iso_max_configure"}:
            raise ValueError(f"Unexpected read-only module exports: {exports}")
    data = module.read_bytes()
    for writer in (b"SetParameter", b"SetUserData", b"GetInstance"):
        if writer in data:
            raise ValueError(f"Unexpected writer or lazy constructor: {writer!r}")
    files = {
        "nx300m-exposure-test/exposure-launch.sh": ROOT / "mod/deployment/mfw/exposure-launch.sh",
        "nx300m-exposure-test/lib/exposure_status.so": module,
        "nx300m-exposure-test/README.md": ROOT / "docs/MFW_EXPOSURE_TEST.md",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    archive_path = OUT / "nx300m-mfw-exposure-test.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in files.items():
            contents = source.read_bytes()
            if source.suffix in {".sh", ".md"}:
                contents = contents.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
            target = OUT / "sd" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents)
    print(f"Built {len(files)} files: {archive_path}")


if __name__ == "__main__":
    main()
