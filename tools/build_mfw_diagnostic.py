"""Prepare an SD diagnostic entry for an already installed NX300M MFW.

Only writes the project output directory. Does not inspect a card or camera.
"""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/mfw-diagnostic"
FILES = {
    "autoexec.sh": ROOT / "mod/deployment/mfw/autoexec.sh",
    "nx300m-diagnostics/preflight.sh": ROOT / "tools/camera_readonly_preflight.sh",
    "README.md": ROOT / "docs/MFW_ENTRY.md",
}


def main():
    staging = OUT / "sd"
    OUT.mkdir(parents=True, exist_ok=True)
    archive_path = OUT / "nx300m-mfw-diagnostic.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in FILES.items():
            data = source.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            mode = 0o100755 if name.endswith(".sh") else 0o100644
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            print(f"{name}: {len(data)} bytes")
    print(archive_path)
    print("Prepared on PC only; this build does not execute camera code. See MFW_ENTRY.md for device results.")


if __name__ == "__main__":
    main()
