"""Export the freshly verified NX300M UBIFS as a metadata-preserving TAR on Windows."""

import io
import json
import mmap
import stat
import tarfile
from pathlib import Path, PurePosixPath

from ubireader.ubi import ubi
from ubireader.ubi_io import ubi_file, leb_virtual_file
from ubireader.ubifs import ubifs, walk
from ubireader.ubifs.decrypt import decrypt_symlink_target
from ubireader.ubifs.misc import process_reg_file
from ubireader.utils import guess_peb_size, guess_start_offset

PROJECT = Path(__file__).resolve().parents[1]


def main():
    directory = PROJECT / "work/stock-1.15"
    binary = directory / "nx300m.bin"
    if not (directory / "source.json").is_file():
        raise RuntimeError("Run prepare_stock.py first")
    start = guess_start_offset(str(binary))
    peb = guess_peb_size(str(binary))
    with binary.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        ends = [offset + peb for offset in range(start, len(data) - peb + 1, peb) if data[offset:offset + 4] == b"UBI#"]
    if not ends: raise RuntimeError("No complete UBI blocks")
    end = max(ends)
    image_file = ubi_file(str(binary), peb, start, end)
    container = ubi(image_file)
    candidates = [(image, image.volumes["rootdir"]) for image in container.images if "rootdir" in image.volumes]
    if len(candidates) != 1: raise RuntimeError("Expected exactly one rootdir volume")
    image, volume = candidates[0]
    filesystem = ubifs(leb_virtual_file(container, volume.get_blocks(container.blocks)))
    inodes, bad_blocks = {}, []
    walk.index(filesystem, filesystem.master_node.root_lnum, filesystem.master_node.root_offs, inodes, bad_blocks)
    if bad_blocks: raise RuntimeError(f"Unreadable UBIFS blocks: {bad_blocks}")
    manifest = {"ubi_offset":start, "ubi_end":end, "peb_size":peb, "image_sequence":image.image_seq,
                "regular_files":0, "directories":0, "symlinks":0, "special":[], "bad_blocks":bad_blocks}
    hardlinks = {}
    output = directory / "rootfs.tar"
    temporary = output.with_suffix(".partial")
    with tarfile.open(temporary, "w") as archive:
        def visit(dent, parent=PurePosixPath(".")):
            name = dent.name
            if name in ("", ".", "..") or "/" in name or "\\" in name:
                raise RuntimeError(f"Unsafe archive name: {name!r}")
            relative = parent / name
            inode = inodes[dent.inum]
            node = inode["ino"]
            info = tarfile.TarInfo(relative.as_posix())
            info.mode = stat.S_IMODE(node.mode)
            info.uid, info.gid, info.mtime = node.uid, node.gid, node.mtime_sec
            mode = node.mode
            if stat.S_ISDIR(mode):
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
                manifest["directories"] += 1
                for child in inode.get("dent", []): visit(child, relative)
            elif stat.S_ISLNK(mode):
                info.type = tarfile.SYMTYPE
                info.linkname = decrypt_symlink_target(filesystem, inodes, dent)
                archive.addfile(info)
                manifest["symlinks"] += 1
            elif stat.S_ISREG(mode):
                if dent.inum in hardlinks:
                    info.type = tarfile.LNKTYPE
                    info.linkname = hardlinks[dent.inum]
                    archive.addfile(info)
                else:
                    payload = process_reg_file(filesystem, inode, relative.as_posix(), inodes)
                    if len(payload) != node.size: raise RuntimeError(f"File size mismatch: {relative}")
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
                    hardlinks[dent.inum] = relative.as_posix()
                manifest["regular_files"] += 1
                if manifest["regular_files"] % 1000 == 0:
                    print(f"Exported {manifest['regular_files']} files", flush=True)
            else:
                manifest["special"].append({"path":relative.as_posix(), "mode":mode})
        for dent in inodes[1]["dent"]: visit(dent)
    image_file.close()
    temporary.replace(output)
    (directory / "rootfs-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    print(f"Exported: {output}", flush=True)


if __name__ == "__main__": main()
