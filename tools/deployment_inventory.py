"""Read-only stock TAR startup/SD inventory; write evidence under outputs/deployment."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment"
PATTERN = re.compile(rb"(?:autoexec[^\x00\r\n ]*|nx_cs\.adj|/mnt/mmc[^\x00\r\n ]*|di-camera-app[^\x00\r\n ]*|runmod[^\x00\r\n ]*|SDH[CT][^\x00\r\n ]*)", re.I)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--member", help="copy and print one original regular member")
    parser.add_argument("--copy-only", action="store_true")
    parser.add_argument("--contains", help="list original regular members containing this UTF-8 byte sequence")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"source":"work/stock-1.15/rootfs.tar", "startup_members":[], "matching_files":[]}
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar", "r") as archive:
        if args.contains:
            needle = args.contains.encode("utf-8")
            matches = []
            for member in archive:
                if not member.isfile():
                    continue
                data = archive.extractfile(member).read()
                if needle in data:
                    matches.append({"path": member.name, "offset": data.find(needle), "bytes": len(data)})
            print(json.dumps({"needle": args.contains, "members": matches}, ensure_ascii=False, indent=2))
            return
        if args.member:
            member = archive.getmember(args.member)
            if not member.isfile():
                raise RuntimeError("member is not a regular file")
            data = archive.extractfile(member).read()
            destination = OUT / "stock" / PurePosixPath(member.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            if args.copy_only:
                print(json.dumps({"member":member.name,"copied_to":str(destination.relative_to(ROOT)),"bytes":len(data)}))
            else:
                print(data.decode("utf-8", "replace"))
            return
        for member in archive:
            name = member.name
            startup = name.startswith(("etc/rc", "etc/init", "etc/udev", "etc/X11")) or name in ("etc/fstab", "etc/profile", "etc/mtab", "etc/~inittab")
            if startup:
                report["startup_members"].append({"path":name,"mode":oct(member.mode),"uid":member.uid,"gid":member.gid,"type":member.type.decode("ascii"),"target":member.linkname})
            if not member.isfile() or member.size > 24 * 1024 * 1024:
                continue
            if not (name.startswith(("etc/", "usr/bin/", "usr/sbin/", "bin/", "sbin/"))):
                continue
            data = archive.extractfile(member).read()
            hits = list(PATTERN.finditer(data))
            if hits:
                strings = []
                for hit in hits:
                    begin = max(data.rfind(b"\0", 0, hit.start()), data.rfind(b"\n", 0, hit.start())) + 1
                    ends = [v for v in (data.find(b"\0", hit.end()), data.find(b"\n", hit.end())) if v >= 0]
                    end = min(ends) if ends else len(data)
                    strings.append({"offset":hit.start(),"text":data[begin:end].decode("utf-8", "replace")[:1000]})
                report["matching_files"].append({"path":name,"bytes":len(data),"hits":strings})
            if startup and b"\0" not in data:
                destination = OUT / "stock" / PurePosixPath(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
    (OUT / "stock-startup-inventory.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"startup_members":len(report["startup_members"]),"matching_files":len(report["matching_files"]),"paths":[v['path'] for v in report['matching_files']]},indent=2))


if __name__ == "__main__":
    main()
