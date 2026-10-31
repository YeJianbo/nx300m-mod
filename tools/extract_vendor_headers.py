"""Read selected driver definitions from the verified original Samsung source archive."""

import argparse
import gzip
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]

DEFINITIONS = re.compile(
    r"(?:project/NX300M/imagedev/usr/include/drime4/(?:udd/(?:pwm|pwm_type|ugpio|gpio_type|d4_lib|u?spi[^/]*|hs_spi[^/]*)|[^/]*sysmmap)\.h$"
    r"|packages_nx300m/linux/(?:include/linux/pwmdev(?:_conf)?\.h$"
    r"|include/media/drime4/(?:sma|bma)/[^/]+\.[ch]$"
    r"|arch/arm/mach-drime4/include/mach/(?:d4_mem|d4_cma|map|d4_ht_pwm[^/]*|pwm_map_d4_nx300|[^/]*spi[^/]*)\.h$"
    r"|include/linux/(?:hs_spi(?:_ioctl)?|spi/d4_spi_config)\.h$"
    r"|drivers/spi/(?:d4[^/]*|drime4[^/]*|hs_spi[^/]*)\.[ch]$"
    r"|drivers/pwm/(?:pwmdev|drime4-pwm|d4_ht_pwm)\.c$"
    r"|drivers/media/video/drime4/(?:sma|bma)/[^/]+\.[ch]$))"
)
DEFINITIONS = re.compile(DEFINITIONS.pattern + r"|packages_nx300m/linux/.*(?:allegro|shutter).*\.[ch]$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|packages_nx300m/linux/(?:drivers/media/video/drime4/(?:pp|ipcm|ipcs|ssif|d4c)(?:/|_).*\.[ch]$"
    r"|include/media/drime4/(?:pp|ipcm|ipcs|ssif|d4c)(?:/|_).*\.[ch]$"
    r"|arch/arm/mach-drime4/include/mach/(?:gpio|gpio_map_d4_nx300|version_information)\.h$)", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern + r"|packages_nx300m/linux/(?:drivers|include)/.*(?:csm|bwm|opener|(?:drime4|d4)[_-]pmu).*\.[ch]$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|packages_nx300m/linux/arch/arm/mach-drime4/dev-(?:pp|bwm|ipcm|ipcs|csm|pmu)[^/]*\.c$"
    r"|packages_nx300m/linux/(?:\.config|arch/arm/configs/[^/]*(?:drime4|nx300)[^/]*defconfig)$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|packages_nx300m/linux/(?:drivers/media/video/drime4/.*mipi.*\.[ch]$"
    r"|include/media/drime4/.*mipi.*\.[ch]$"
    r"|arch/arm/mach-drime4/(?:dev-mipi[^/]*\.c|include/mach/[^/]*mipi[^/]*\.h)$)"
    r"|project/NX300M/imagedev/usr/include/drime4/udd/[^/]*mipi[^/]*\.h$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|project/NX300M/imagedev/usr/include/drime4/(?:udd|ipcm|ipcs)/[^/]*ipc[ms][^/]*\.h$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|packages_nx300m/linux/drivers/video/(?:drime4|drime4_fb|d4_dp)/.*\.[ch]$"
    r"|packages_nx300m/linux/include/(?:linux|video)/(?:d4[_-](?:dp|fb|lcd)|drime4[_-]?fb)[^/]*\.h$"
    r"|project/NX300M/imagedev/usr/include/drime4/udd/(?:dp|lcd|fb)[^/]*\.h$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|packages_nx300m/linux/include/video/drime4/[^/]+\.h$"
    r"|packages_nx300m/linux/drivers/video/(?:drime4[^/]*|d4_[^/]*)\.[ch]$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|packages_nx300m/linux/(?:drivers|include)/.*(?:/ep/|/ep_|/d4_ep)[^ ]*\.[ch]$"
    r"|project/NX300M/imagedev/usr/include/drime4/udd/ep[^/]*\.h$", re.IGNORECASE)
DEFINITIONS = re.compile(DEFINITIONS.pattern +
    r"|project/NX300M/imagedev/usr/include/drime4/udd/(?:pp[^/]*|secc[^/]*)\.h$", re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    prefix = (PROJECT / "work/downloads/source-range.bin").read_bytes()
    with args.archive.open("rb") as stream:
        if stream.read(len(prefix)) != prefix or args.archive.stat().st_size != 638285114:
            raise RuntimeError("Source archive differs from the fetched vendor-source mirror")
    destination = PROJECT / "work/vendor-source"
    destination.mkdir(parents=True, exist_ok=True)
    old_manifest = destination / 'SOURCE.json'
    if old_manifest.exists():
        for entry in json.loads(old_manifest.read_text(encoding='utf-8'))['files']:
            if DEFINITIONS.search(entry['path']): continue
            target = (destination / entry['path']).resolve()
            target.relative_to(destination.resolve())
            if target.is_file(): target.unlink()
    manifest = {"archive":str(args.archive), "url":"https://nxfiles.nx.tc/files/NX300M/Source/nx300m_2014-06-27.tar.gz", "files":[]}
    with args.archive.open("rb") as compressed, gzip.GzipFile(fileobj=compressed) as stream:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for entry in archive:
                if entry.isfile() and DEFINITIONS.search(entry.name):
                    relative = PurePosixPath(entry.name)
                    if relative.is_absolute() or ".." in relative.parts: raise RuntimeError("Unsafe member")
                    target = destination / relative.as_posix()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(entry).read())
                    manifest["files"].append({"path":entry.name, "size":entry.size})
                    print(entry.name, flush=True)
        while stream.read(1024 * 1024): pass
    manifest["gzip_crc_verified"] = True
    (destination / "SOURCE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Verified source archive; extracted {len(manifest['files'])} definitions", flush=True)


if __name__ == "__main__": main()
