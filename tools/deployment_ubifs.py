"""Inspect stock UBIFS and build a separate, metadata-checked userspace candidate.

Windows entrypoints use the project Conda interpreter. The internal Linux staging
entrypoint uses Debian's Python; it does not need ubi-reader or a virtualenv.
"""

import argparse
import base64
import copy
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import zlib

from build_stock_app_loader import CAMERA_DESKTOP, PERSISTENT_HOOKS

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "outputs/deployment/ubifs"
STOCK = PROJECT / "work/stock-1.15/nx300m.bin"
TAR = PROJECT / "work/stock-1.15/rootfs.tar"
INODE_FIELDS = (
    "creat_sqnum size atime_sec ctime_sec mtime_sec atime_nsec ctime_nsec "
    "mtime_nsec nlink uid gid mode flags data_len xattr_cnt xattr_size "
    "xattr_names compr_type"
).split()
# Sequence numbers and inode numbers describe the physical index, not pathname
# semantics; all other inode fields are compared or restored explicitly.
SEMANTIC_FIELDS = [x for x in INODE_FIELDS if x != "creat_sqnum"]
SB_FIELDS = (
    "key_hash key_fmt flags min_io_size leb_size leb_cnt max_leb_cnt max_bud_bytes "
    "log_lebs lpt_lebs orph_lebs jhead_cnt fanout lsave_cnt fmt_version "
    "default_compr rp_uid rp_gid rp_size time_gran ro_compat_version"
).split()
OVERLAY_MODES = {
    "usr/bin/hib2.sh": None,
    CAMERA_DESKTOP: None,
    "usr/bin/nx300m-camera-launch": 0o755,
    "usr/lib/nx300m-mod/preload.sh": 0o644,
    **{"usr/lib/nx300m-mod/" + name: 0o644 for name in PERSISTENT_HOOKS},
}
OVERLAY_FILES = list(OVERLAY_MODES)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encode(data):
    return base64.b64encode(data).decode("ascii")


def safe_path(name):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or "\0" in name:
        raise ValueError(f"Unsafe path: {name!r}")
    return p.as_posix()


def wsl_path(path):
    p = str(Path(path).resolve())
    return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")


class Image:
    def __init__(self, path, stock=False):
        from ubireader.ubi import ubi
        from ubireader.ubi_io import ubi_file, leb_virtual_file
        from ubireader.ubifs import ubifs, walk
        from ubireader.utils import guess_peb_size, guess_start_offset
        self.path = Path(path)
        if stock:
            start, peb = guess_start_offset(str(path)), guess_peb_size(str(path))
            with path.open("rb") as stream:
                stream.seek(start)
                count = 0
                while stream.read(4) == b"UBI#":
                    count += 1
                    stream.seek(start + count * peb)
            end = start + count * peb
            self.file = ubi_file(str(path), peb, start, end)
            self.ubi = ubi(self.file)
            candidates = [(im, v) for im in self.ubi.images for name, v in im.volumes.items() if name == "rootdir"]
            if len(candidates) != 1:
                raise ValueError("Expected one rootdir volume")
            self.image, self.volume = candidates[0]
            self.fs = ubifs(leb_virtual_file(self.ubi, self.volume.get_blocks(self.ubi.blocks)))
            blocks = self.volume.get_blocks(self.ubi.blocks)
            first = next(iter(blocks.values()))
            self.geometry = {
                "ubi_start": start, "ubi_end": end, "peb_size": peb,
                "image_sequence": self.image.image_seq,
                "vol_id": self.volume.vol_id, "volume_name": "rootdir",
                "volume_record": {k: (v.hex() if isinstance(v, bytes) else v)
                                  for k, v in self.volume.vol_rec if not callable(v)},
                "vid_hdr_offset": first.ec_hdr.vid_hdr_offset,
                "data_offset": first.ec_hdr.data_offset,
                "erase_counters": sorted({b.ec_hdr.ec for b in self.ubi.blocks.values()}),
                "ubi_versions": sorted({b.ec_hdr.version for b in self.ubi.blocks.values()}),
            }
        else:
            with path.open("rb") as stream:
                stream.seek(36)
                leb_size = struct.unpack("<I", stream.read(4))[0]
            self.file = ubi_file(str(path), leb_size)
            self.fs = ubifs(self.file)
            self.geometry = {}
        self.inodes, self.bad = {}, []
        walk.index(self.fs, self.fs.master_node.root_lnum, self.fs.master_node.root_offs, self.inodes, self.bad)
        if self.bad:
            raise ValueError(f"Unreadable LEBs: {self.bad}")
        self.locations, self.crc_counts = {}, Counter()
        self.read_node(0)
        self._validate_index(self.fs.master_node.root_lnum, self.fs.master_node.root_offs)
        self.paths = {".": 1}
        self._paths(1, PurePosixPath("."), set())

    def _paths(self, ino, parent, ancestors):
        if ino in ancestors:
            raise ValueError("Directory cycle")
        for dent in self.inodes[ino].get("dent", []):
            if not dent.inum:
                raise ValueError("Deleted directory entry in committed index")
            name = dent.name
            if name in ("", ".", "..") or "/" in name:
                raise ValueError(f"Invalid directory entry {name!r}")
            path = safe_path((parent / name).as_posix())
            if path in self.paths:
                raise ValueError(f"Duplicate path: {path}")
            self.paths[path] = dent.inum
            if stat.S_ISDIR(self.inodes[dent.inum]["ino"].mode):
                self._paths(dent.inum, parent / name, ancestors | {ino})

    def read_node(self, offset):
        from ubireader.ubifs import nodes
        from ubireader.ubifs.defines import UBIFS_COMMON_HDR_SZ
        self.fs.file.seek(offset)
        head = self.fs.file.read(UBIFS_COMMON_HDR_SZ)
        hdr = nodes.common_hdr(head)
        if hdr.len < UBIFS_COMMON_HDR_SZ or hdr.len > self.fs.leb_size:
            raise ValueError(f"Invalid node length at {offset}")
        body = self.fs.file.read(hdr.len - UBIFS_COMMON_HDR_SZ)
        raw = head + body
        if len(raw) != hdr.len or (zlib.crc32(raw[8:]) ^ 0xffffffff) != hdr.crc:
            raise ValueError(f"UBIFS CRC mismatch at {offset}")
        return hdr, raw

    def _validate_index(self, lnum, offs):
        from ubireader.ubifs import nodes
        from ubireader.ubifs.defines import UBIFS_IDX_NODE, UBIFS_INO_NODE
        address = lnum * self.fs.leb_size + offs
        hdr, raw = self.read_node(address)
        self.crc_counts[hdr.node_type] += 1
        if hdr.node_type == UBIFS_IDX_NODE:
            index = nodes.idx_node(raw[24:])
            for b in index.branches:
                self._validate_index(b.lnum, b.offs)
        elif hdr.node_type == UBIFS_INO_NODE:
            inode = nodes.ino_node(raw[24:])
            self.locations[inode.key["ino_num"]] = address

    def payload(self, ino):
        from ubireader.ubifs.misc import process_reg_file
        return process_reg_file(self.fs, self.inodes[ino], str(ino), self.inodes)

    def manifest(self, contents=True):
        by_inode = defaultdict(list)
        for path, ino in self.paths.items():
            by_inode[ino].append(path)
        by_inode = dict(by_inode)
        records = {}
        xattr_inodes = set()
        counts = Counter()
        compression = Counter()
        for number, ino in sorted(self.inodes.items()):
            node = ino["ino"]
            record = {k: getattr(node, k) for k in INODE_FIELDS}
            record.update({"paths": sorted(by_inode.get(number, [])), "inline_data_base64": encode(node.data),
                           "padding1_base64": encode(node.padding1), "padding2_base64": encode(node.padding2),
                           "node_offset": self.locations[number], "xattrs": []})
            for entry in ino.get("xent", []):
                target = self.inodes[entry.inum]["ino"]
                if len(target.data) != target.data_len:
                    raise ValueError("Unexpected xattr storage")
                xattr_inodes.add(entry.inum)
                record["xattrs"].append({"name": entry.name, "inode": entry.inum, "value_base64": encode(target.data)})
            if len(record["xattrs"]) != node.xattr_cnt:
                raise ValueError(f"xattr count mismatch inode {number}")
            for data in ino.get("data", []):
                compression[data.compr_type] += 1
            if by_inode.get(number):
                mode = node.mode
                kind = "regular" if stat.S_ISREG(mode) else "directory" if stat.S_ISDIR(mode) else "symlink" if stat.S_ISLNK(mode) else "special"
                record["kind"] = kind
                counts[kind] += len(by_inode[number])
                if kind == "regular" and contents:
                    data = self.payload(number)
                    if len(data) != node.size:
                        raise ValueError(f"Content length mismatch inode {number}")
                    record["sha256"] = hashlib.sha256(data).hexdigest()
                if kind == "symlink":
                    record["target"] = node.data.decode("utf-8")
                if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                    # UBIFS's union ubifs_dev_desc stores new_encode_dev in its
                    # first 32 bits, not the host's libc dev_t layout.
                    dev = struct.unpack_from("<I", node.data)[0]
                    record["device_major"] = (dev & 0xfff00) >> 8
                    record["device_minor"] = (dev & 0xff) | ((dev >> 12) & 0xfff00)
            records[str(number)] = record
        unreferenced = set(self.inodes) - set(by_inode) - xattr_inodes
        if unreferenced:
            raise ValueError(f"Unreferenced committed inodes: {sorted(unreferenced)}")
        sb = self.fs.superblock_node
        return {"source": str(self.path), "geometry": self.geometry,
                "superblock": {k: getattr(sb, k) for k in SB_FIELDS}, "uuid_hex": sb.uuid.hex(),
                "master_flags": self.fs.master_node.flags,
                "index_node_crc_counts": dict(self.crc_counts), "bad_blocks": self.bad,
                "counts": dict(counts), "data_node_compression_counts": dict(compression),
                "inode_count": len(records), "xattr_inode_count": len(xattr_inodes),
                "paths": self.paths, "inodes": records}

    def close(self):
        self.file.close()


def verify_tar(manifest):
    expected = {p: manifest["inodes"][str(i)] for p, i in manifest["paths"].items() if p != "."}
    seen = set()
    with tarfile.open(TAR, "r") as archive:
        for member in archive:
            path = safe_path(member.name)
            if path in seen or path not in expected:
                raise ValueError(f"Unexpected TAR member {path}")
            seen.add(path)
            record = expected[path]
            if (member.mode, member.uid, member.gid, member.mtime) != (
                    stat.S_IMODE(record["mode"]), record["uid"], record["gid"], record["mtime_sec"]):
                raise ValueError(f"TAR metadata mismatch: {path}")
            if record["kind"] == "regular":
                if not (member.isfile() or member.islnk()):
                    raise ValueError(f"Wrong TAR type: {path}")
                with archive.extractfile(member) as data:
                    if hashlib.file_digest(data, "sha256").hexdigest() != record["sha256"]:
                        raise ValueError(f"TAR content mismatch: {path}")
                if member.islnk() and member.linkname not in record["paths"]:
                    raise ValueError(f"Wrong hard link: {path}")
            elif record["kind"] == "symlink" and (not member.issym() or member.linkname != record["target"]):
                raise ValueError(f"Wrong symlink: {path}")
            elif record["kind"] == "directory" and not member.isdir():
                raise ValueError(f"Wrong directory: {path}")
    if seen != set(expected):
        raise ValueError(f"TAR missing paths: {sorted(set(expected)-seen)}")
    return {"members": len(seen), "all_regular_content_equal_to_direct_ubifs": True,
            "mode_uid_gid_mtime_seconds_links_verified": True,
            "root_inode_and_remaining_metadata_source": "stock-metadata.json"}


def inspect():
    OUT.mkdir(parents=True, exist_ok=True)
    provenance = json.loads((PROJECT / "work/stock-1.15/source.json").read_text(encoding="utf-8"))
    identity = digest_file(STOCK)
    if identity != provenance["local_binary_sha256"]:
        raise ValueError("Stock binary differs from verified source.json")
    image = Image(STOCK, stock=True)
    try:
        manifest = image.manifest()
        manifest["source_sha256"] = identity
        manifest["tar_verification"] = verify_tar(manifest)
        save(OUT / "stock-metadata.json", manifest)
        print(json.dumps({k: manifest[k] for k in ("geometry", "superblock", "counts", "inode_count", "xattr_inode_count", "data_node_compression_counts", "tar_verification")}, indent=2), flush=True)
    finally:
        image.close()


def with_overlay(stock_manifest, overlay, output, overlay_modes=None):
    """Describe an explicitly declared overlay, preserving replacement metadata.

    The default remains the persistent app-loader whitelist. Dedicated builders
    can supply a smaller whitelist without changing that installation profile.
    """
    modes = OVERLAY_MODES if overlay_modes is None else overlay_modes
    overlay_files = list(modes)
    overlay = Path(overlay).resolve()
    files = {safe_path(p.relative_to(overlay).as_posix()) for p in overlay.rglob("*") if p.is_file()}
    if files != set(overlay_files) or any(p.is_symlink() for p in overlay.rglob("*")):
        raise ValueError(f"Expected exactly the declared overlay ({len(overlay_files)} files), got {sorted(files)}")
    candidate_report = overlay.parent / "report.json"
    declared = json.loads(candidate_report.read_text(encoding="utf-8"))["rootfs_changes"]
    declarations = {safe_path(x["destination"].lstrip("/")): x for x in declared}
    if len(declarations) != len(declared) or set(declarations) != set(overlay_files):
        raise ValueError("Candidate rootfs_changes differs from the explicit overlay whitelist")
    expected = copy.deepcopy(stock_manifest)
    snapshot = output / "input-overlay"
    previous_files = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()}
    snapshot.mkdir(parents=True, exist_ok=True)
    source_report = []
    previous_comparison = {"added_paths": [], "changed_paths": [], "unchanged_paths": [],
                           "removed_paths": sorted(previous_files - files)}
    timestamp = expected["inodes"][str(expected["paths"]["usr/lib"])]["mtime_sec"]
    # UBIFS directory i_size is its 160-byte inode node plus each aligned dent.
    def dent_size(name):
        return (24 + 32 + len(name.encode("utf-8")) + 1 + 7) & ~7
    template = expected["inodes"][str(expected["paths"]["usr/bin/hib2.sh"])].copy()
    next_inode = max(map(int, expected["inodes"])) + 1
    new_files = set(overlay_files) - set(stock_manifest["paths"])
    new_directories = set()
    for relative in new_files:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() not in stock_manifest["paths"]:
            new_directories.add(parent.as_posix())
            parent = parent.parent

    def add_entry(relative, is_directory=False):
        parent_name = PurePosixPath(relative).parent.as_posix()
        parent = expected["inodes"][str(expected["paths"][parent_name])]
        if not stat.S_ISDIR(parent["mode"]):
            raise ValueError(f"Overlay parent is not a directory: {parent_name}")
        parent["size"] += dent_size(PurePosixPath(relative).name)
        parent["nlink"] += int(is_directory)

    for directory in sorted(new_directories, key=lambda p: (len(PurePosixPath(p).parts), p)):
        record = template.copy()
        record.update({"kind": "directory", "mode": stat.S_IFDIR | 0o755, "size": 160,
                       "nlink": 2, "uid": 0, "gid": 0, "paths": [directory],
                       "atime_sec": timestamp, "mtime_sec": timestamp, "ctime_sec": timestamp,
                       "atime_nsec": 0, "mtime_nsec": 0, "ctime_nsec": 0,
                       "creat_sqnum": 0, "node_offset": None})
        record.pop("sha256", None)
        expected["paths"][directory] = next_inode
        expected["inodes"][str(next_inode)] = record
        next_inode += 1
        add_entry(directory, is_directory=True)
    for relative in overlay_files:
        declaration = declarations[relative]
        metadata = declaration["metadata"]
        mode, uid, gid = int(metadata["mode"], 8), metadata["uid"], metadata["gid"]
        source = overlay / relative
        payload = source.read_bytes()
        if len(payload) != declaration["bytes"]:
            raise ValueError(f"Candidate byte count differs from its report: {relative}")
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        previous_comparison["added_paths" if relative not in previous_files else
                            "unchanged_paths" if target.read_bytes() == payload else "changed_paths"].append(relative)
        target.write_bytes(payload)
        identity = hashlib.sha256(payload).hexdigest()
        if relative in expected["paths"]:
            record = expected["inodes"][str(expected["paths"][relative])]
            if record["kind"] != "regular" or record["paths"] != [relative]:
                raise ValueError("Replacement must be a single-path regular file")
            if modes[relative] is not None or declaration["change"] != "modified":
                raise ValueError(f"Unexpected replacement role: {relative}")
            if (mode, uid, gid, metadata["mtime"]) != (stat.S_IMODE(record["mode"]), record["uid"], record["gid"], record["mtime_sec"]):
                raise ValueError(f"Replacement metadata differs from stock: {relative}")
        else:
            if (mode, uid, gid) != (modes[relative], 0, 0) or declaration["change"] != "new":
                raise ValueError(f"New file metadata differs from its declared role: {relative}")
            file_timestamp = metadata.get("mtime", timestamp)
            record = template.copy()
            record.update({"paths": [relative], "mode": stat.S_IFREG | mode, "uid": uid, "gid": gid,
                           "nlink": 1, "atime_sec": file_timestamp, "mtime_sec": file_timestamp, "ctime_sec": file_timestamp,
                           "atime_nsec": 0, "mtime_nsec": 0, "ctime_nsec": 0,
                           "creat_sqnum": 0, "node_offset": None})
            expected["paths"][relative] = next_inode
            expected["inodes"][str(next_inode)] = record
            next_inode += 1
            add_entry(relative)
        record.update({"size": len(payload), "sha256": identity, "payload_source": wsl_path(target)})
        source_report.append({"path": relative, "source": str(source), "size": len(payload), "sha256": identity,
                              "mode": oct(stat.S_IMODE(record["mode"])), "uid": record["uid"], "gid": record["gid"],
                              "mtime": record["mtime_sec"], "candidate_change": declaration})
    differences = []
    for path, number in expected["paths"].items():
        record = expected["inodes"][str(number)]
        if path not in stock_manifest["paths"]:
            differences.append({"path": path, "change": "added", "metadata": {
                k: record[k] for k in SEMANTIC_FIELDS + ["kind", "sha256"] if k in record}})
        else:
            original = stock_manifest["inodes"][str(stock_manifest["paths"][path])]
            fields = {k: {"stock": original.get(k), "expected": record.get(k)}
                      for k in SEMANTIC_FIELDS + ["sha256"] if original.get(k) != record.get(k)}
            if fields:
                differences.append({"path": path, "change": "modified", "fields": fields})
    expected["overlay"] = {"source": str(overlay), "files": source_report,
                           "persistent_hooks": list(PERSISTENT_HOOKS) if overlay_modes is None else [],
                           "file_count": len(overlay_files), "new_library_count": len(PERSISTENT_HOOKS) if overlay_modes is None else 0,
                           "new_file_count": len(new_files), "new_directory_count": len(new_directories),
                           "new_directory_mode": "0755", "new_uid": 0, "new_gid": 0,
                           "new_timestamp_policy": "candidate per-file mtime where declared; otherwise stock /usr/lib mtime; new atime/ctime equal mtime",
                           "new_directory_timestamps": timestamp,
                           "replacement_metadata": "preserve each original inode except size/content",
                           "candidate_report_source": str(candidate_report),
                           "comparison_with_previous_input_overlay": previous_comparison,
                           "expected_stock_differences": differences}
    expected["inode_count"] = len(expected["inodes"])
    expected["counts"]["directory"] += len(new_directories)
    expected["counts"]["regular"] += len(new_files)
    expected["manifest_role"] = "expected modified filesystem from stock metadata and explicit overlay"
    for key in ("tar_verification", "index_node_crc_counts", "data_node_compression_counts"):
        expected["base_stock_" + key] = expected.pop(key)
    save(output / "expected-metadata.json", expected)
    save(output / "overlay-input-report.json", expected["overlay"])
    return expected


def linux_stage(args):
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("Internal staging requires WSL root")
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    # A new ext4 temp directory avoids Windows mode/xattr/device semantics and
    # never borrows the running rootfs or an existing mount namespace.
    stage = Path(tempfile.mkdtemp(prefix="nx300m-mod-ubifs-"))
    try:
        dirs = [(p, i) for p, i in manifest["paths"].items() if stat.S_ISDIR(manifest["inodes"][str(i)]["mode"])]
        for path, _ in sorted(dirs, key=lambda x: len(PurePosixPath(x[0]).parts)):
            (stage / safe_path(path)).mkdir(exist_ok=True)
        with tarfile.open(args.tar, "r") as archive:
            for number, record in manifest["inodes"].items():
                if not record["paths"] or record["kind"] == "directory":
                    continue
                first, *others = record["paths"]
                target = stage / safe_path(first)
                if record["kind"] == "regular":
                    source = Path(record["payload_source"]).open("rb") if "payload_source" in record else archive.extractfile(first)
                    with source, target.open("xb") as dest:
                        shutil.copyfileobj(source, dest)
                elif record["kind"] == "symlink":
                    target.symlink_to(record["target"])
                elif stat.S_ISFIFO(record["mode"]):
                    os.mkfifo(target, stat.S_IMODE(record["mode"]))
                elif stat.S_ISCHR(record["mode"]) or stat.S_ISBLK(record["mode"]):
                    os.mknod(target, record["mode"], os.makedev(record["device_major"], record["device_minor"]))
                else:
                    raise ValueError(f"Unsupported special inode {number}")
                for path in others:
                    os.link(target, stage / safe_path(path), follow_symlinks=False)
        for path, number in sorted(manifest["paths"].items(), key=lambda x: -len(PurePosixPath(x[0]).parts)):
            record = manifest["inodes"][str(number)]
            target = stage / safe_path(path)
            os.chown(target, record["uid"], record["gid"], follow_symlinks=False)
            if record["kind"] != "symlink":
                os.chmod(target, stat.S_IMODE(record["mode"]))
            for attr in record["xattrs"]:
                os.setxattr(target, attr["name"], base64.b64decode(attr["value_base64"]), follow_symlinks=False)
            os.utime(target, ns=(record["atime_sec"]*10**9 + record["atime_nsec"], record["mtime_sec"]*10**9 + record["mtime_nsec"]), follow_symlinks=False)
        sb = manifest["superblock"]
        compressors = {0: "none", 1: "lzo", 2: "zlib"}
        if sb["default_compr"] not in compressors:
            raise ValueError("Unsupported stock compression")
        command = [args.mkfs, "-r", str(stage), "-o", args.candidate,
                   "-m", str(sb["min_io_size"]), "-e", str(sb["leb_size"]),
                   "-c", str(sb["max_leb_cnt"]), "-x", compressors[sb["default_compr"]],
                   "-j", str(sb["max_bud_bytes"]), "-l", str(sb["log_lebs"]),
                   "-f", str(sb["fanout"]), "-k", "r5" if sb["key_hash"] == 0 else "test",
                   "-p", str(sb["orph_lebs"]), "-R", str(sb["rp_size"])]
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = args.libdir
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        save(Path(args.log), {"command": command, "returncode": result.returncode,
                              "stdout": result.stdout, "stderr": result.stderr,
                              "staging": str(stage), "staging_removed_after_build": True})
        if result.returncode:
            raise RuntimeError(f"mkfs.ubifs failed ({result.returncode}): {result.stderr}")
    finally:
        shutil.rmtree(stage)


def patch_metadata(path, stock_manifest, normalize_persistent_modes=False):
    """Restore fields mkfs cannot express, without changing indexed node lengths.

    An unauthenticated UBIFS indexes keys and locations. Inode metadata changes
    therefore require only the inode node CRC, not index or LPT modifications.
    Never change nlink/size/xattr lengths to hide a structural mismatch.
    """
    from ubireader.ubifs.defines import (UBIFS_INO_NODE_FORMAT, UBIFS_INO_NODE_FIELDS, UBIFS_INO_NODE_SZ,
                                       UBIFS_SB_NODE_FORMAT, UBIFS_SB_NODE_FIELDS, UBIFS_SB_NODE_SZ)
    image = Image(path)
    candidate_paths = image.paths
    if set(candidate_paths) != set(stock_manifest["paths"]):
        raise ValueError("Candidate path set differs from expected metadata")
    changes = []
    patches = []
    patchable = {"atime_sec", "ctime_sec", "mtime_sec", "atime_nsec", "ctime_nsec", "mtime_nsec", "flags", "compr_type"}
    by_inode = defaultdict(list)
    for pathname, number in candidate_paths.items():
        by_inode[number].append(pathname)
    for ino, nodeinfo in image.inodes.items():
        paths = sorted(by_inode[ino])
        if not paths:
            raise ValueError("Unexpected xattr-only candidate inode; patching needs explicit mapping")
        original = stock_manifest["inodes"][str(stock_manifest["paths"][paths[0]])]
        if paths != original["paths"]:
            raise ValueError(f"Hard link group mismatch: {paths[0]}")
        node = nodeinfo["ino"]
        hdr, raw = image.read_node(image.locations[ino])
        fields = list(struct.unpack(UBIFS_INO_NODE_FORMAT, raw[24:24+UBIFS_INO_NODE_SZ]))
        changed = {}
        for key in SEMANTIC_FIELDS:
            old, wanted = getattr(node, key), original[key]
            if old != wanted:
                authorized_mode_fix = (
                    normalize_persistent_modes and key == "mode" and len(paths) == 1
                    and paths[0] in {"usr/lib/nx300m-mod/" + name for name in PERSISTENT_HOOKS}
                    and old == stat.S_IFREG | 0o755 and wanted == stat.S_IFREG | 0o644
                )
                if key not in patchable and not authorized_mode_fix:
                    raise ValueError(f"Structural metadata differs: {paths[0]} {key}: {old} != {wanted}")
                fields[UBIFS_INO_NODE_FIELDS.index(key)] = wanted
                changed[key] = {"mkfs": old, "expected": wanted}
        if changed:
            replacement = bytearray(raw)
            replacement[24:24+UBIFS_INO_NODE_SZ] = struct.pack(UBIFS_INO_NODE_FORMAT, *fields)
            struct.pack_into("<I", replacement, 4, zlib.crc32(replacement[8:]) ^ 0xffffffff)
            patches.append((image.locations[ino], replacement))
            changes.append({"path": paths[0], "fields": changed})
    _, raw_sb = image.read_node(0)
    sb_fields = list(struct.unpack(UBIFS_SB_NODE_FORMAT, raw_sb[24:24+UBIFS_SB_NODE_SZ]))
    sb_fields[UBIFS_SB_NODE_FIELDS.index("uuid")] = bytes.fromhex(stock_manifest["uuid_hex"])
    replacement = bytearray(raw_sb)
    replacement[24:24+UBIFS_SB_NODE_SZ] = struct.pack(UBIFS_SB_NODE_FORMAT, *sb_fields)
    struct.pack_into("<I", replacement, 4, zlib.crc32(replacement[8:]) ^ 0xffffffff)
    if replacement != raw_sb:
        patches.append((0, replacement))
    image.close()
    with path.open("r+b") as output:
        for offset, data in patches:
            output.seek(offset)
            output.write(data)
    return changes


def compare(candidate, stock_manifest, report_dir=OUT):
    image = Image(candidate)
    try:
        actual = image.manifest()
    finally:
        image.close()
    mismatches = []
    if set(actual["paths"]) != set(stock_manifest["paths"]):
        mismatches.append({"field": "path_set"})
    for path, stock_ino in stock_manifest["paths"].items():
        if path not in actual["paths"]:
            continue
        expected = stock_manifest["inodes"][str(stock_ino)]
        observed = actual["inodes"][str(actual["paths"][path])]
        for key in SEMANTIC_FIELDS + ["paths", "inline_data_base64", "sha256", "target"]:
            if expected.get(key) != observed.get(key):
                mismatches.append({"path": path, "field": key, "stock": expected.get(key), "candidate": observed.get(key)})
        def attrs(record):
            return sorted((a["name"], a["value_base64"]) for a in record["xattrs"])
        if attrs(expected) != attrs(observed):
            mismatches.append({"path": path, "field": "xattrs"})
    # Used LEB count, LPT size and save-table count can change with physical
    # packing. Format flags are not silently accepted if modern mkfs adds one.
    for key in SB_FIELDS:
        if key in ("leb_cnt", "lpt_lebs", "lsave_cnt"):
            continue
        if actual["superblock"][key] != stock_manifest["superblock"][key]:
            mismatches.append({"field": "superblock."+key, "stock": stock_manifest["superblock"][key], "candidate": actual["superblock"][key]})
    if actual["uuid_hex"] != stock_manifest["uuid_hex"]:
        mismatches.append({"field": "uuid"})
    report = {"candidate": str(candidate), "candidate_size": candidate.stat().st_size,
              "candidate_sha256": digest_file(candidate),
              "comparison": "pathname content and semantic inode metadata; physical inode IDs/index placement/creation sequence may change",
              "path_count": len(actual["paths"]), "counts": actual["counts"],
              "index_node_crc_counts": actual["index_node_crc_counts"],
              "stock_superblock": stock_manifest["superblock"], "candidate_superblock": actual["superblock"],
              "mismatches": mismatches, "passed": not mismatches,
              "not_verified": ["kernel mount", "camera execution", "upgrader acceptance"]}
    if "overlay" in stock_manifest:
        report["expected_stock_differences"] = stock_manifest["overlay"]["expected_stock_differences"]
        report["unexpected_difference_count"] = len(mismatches)
        report["overlay_file_metadata"] = {}
        for file in stock_manifest["overlay"]["files"]:
            path = file["path"]
            observed = actual["inodes"][str(actual["paths"][path])]
            report["overlay_file_metadata"][path] = {
                "mode": oct(stat.S_IMODE(observed["mode"])), "uid": observed["uid"], "gid": observed["gid"],
                "mtime": observed["mtime_sec"], "size": observed["size"], "sha256": observed.get("sha256"),
            }
    save(report_dir / "comparison.json", report)
    return report


def build(overlay=None, *, output_dir=None, overlay_modes=None):
    metadata = OUT / "stock-metadata.json"
    if not metadata.is_file():
        inspect()
    manifest = json.loads(metadata.read_text(encoding="utf-8"))
    if digest_file(STOCK) != manifest["source_sha256"]:
        raise ValueError("Stock identity changed")
    # Revalidate reused TAR content; its own hash is neither needed nor used.
    verify_tar(manifest)
    if overlay_modes is not None and overlay is None:
        raise ValueError("An explicit overlay whitelist requires an overlay directory")
    output = Path(output_dir).resolve() if output_dir is not None else OUT / "mod" if overlay else OUT
    output.mkdir(parents=True, exist_ok=True)
    if overlay:
        manifest = with_overlay(manifest, overlay, output, overlay_modes)
        metadata = output / "expected-metadata.json"
    root = OUT / "toolchain/root"
    mkfs = root / "usr/sbin/mkfs.ubifs"
    candidate = output / ("rootdir-mod-candidate.ubifs" if overlay else "rootdir-stock-candidate.ubifs")
    if not mkfs.is_file():
        raise FileNotFoundError("Extract official Debian mtd-utils and libiniparser4 packages under toolchain/root")
    command = ["wsl", "-d", "Debian", "-u", "root", "--", "python3", wsl_path(Path(__file__)),
               "stage-linux", "--manifest", wsl_path(metadata), "--tar", wsl_path(TAR),
               "--mkfs", wsl_path(mkfs), "--libdir", wsl_path(root / "usr/lib/x86_64-linux-gnu"),
               "--candidate", wsl_path(candidate), "--log", wsl_path(output / "mkfs-report.json")]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    (output / "build.stdout.log").write_text(result.stdout, encoding="utf-8")
    (output / "build.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"WSL staging/build failed ({result.returncode}): {result.stderr}")
    changes = patch_metadata(candidate, manifest)
    save(output / "restored-inode-fields.json", {"inode_nodes_changed": len(changes), "changes": changes})
    report = compare(candidate, manifest, output)
    print(json.dumps({k: report[k] for k in ("candidate", "candidate_size", "path_count", "counts", "mismatches", "passed")}, indent=2), flush=True)
    if not report["passed"]:
        raise RuntimeError("Candidate differs; see comparison.json")
    wrap(mod=bool(overlay), output_dir=output)
    return report


def wrap(mod=False, *, output_dir=None):
    """Wrap the checked userspace image in an independent UBI container."""
    from ubireader.ubi import ubi
    from ubireader.ubi_io import ubi_file
    manifest = json.loads((OUT / "stock-metadata.json").read_text(encoding="utf-8"))
    output_dir = Path(output_dir).resolve() if output_dir is not None else OUT / "mod" if mod else OUT
    comparison = json.loads((output_dir / "comparison.json").read_text(encoding="utf-8"))
    if not comparison["passed"]:
        raise ValueError("The UBIFS comparison must pass before wrapping")
    candidate = output_dir / ("rootdir-mod-candidate.ubifs" if mod else "rootdir-stock-candidate.ubifs")
    if digest_file(candidate) != comparison["candidate_sha256"]:
        raise ValueError("Candidate changed after its content/metadata comparison")
    geometry = manifest["geometry"]
    record = geometry["volume_record"]
    config = output_dir / "ubinize.ini"
    # Explicitly recreate the observed header geometry. The original NAND's
    # physical subpage size is not inferable merely from these image bytes.
    text = ("[rootdir]\nmode=ubi\nimage=" + wsl_path(candidate) +
            f"\nvol_id={geometry['vol_id']}\nvol_type=dynamic\nvol_name=rootdir" +
            f"\nvol_alignment={record['alignment']}\nvol_size={record['reserved_pebs']*manifest['superblock']['leb_size']}\nvol_flags=autoresize\n")
    if record["vol_type"] != 1 or record["flags"] != 1 or record["upd_marker"]:
        raise ValueError("Unsupported volume record; no implicit reconstruction")
    config.write_text(text, encoding="utf-8", newline="\n")
    toolroot = OUT / "toolchain/root"
    output = output_dir / ("platform-mod-candidate.ubi" if mod else "platform-stock-candidate.ubi")
    command = ["wsl", "-d", "Debian", "--", "env",
               "LD_LIBRARY_PATH=" + wsl_path(toolroot / "usr/lib/x86_64-linux-gnu"),
               wsl_path(toolroot / "usr/sbin/ubinize"),
               "-o", wsl_path(output), "-m", str(manifest["superblock"]["min_io_size"]),
               "-p", str(geometry["peb_size"]), "-s", str(manifest["superblock"]["min_io_size"]),
               "-O", str(geometry["vid_hdr_offset"]), "-Q", str(geometry["image_sequence"]),
               "-e", str(geometry["erase_counters"][0]), "-x", str(geometry["ubi_versions"][0]), wsl_path(config)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    report = {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    if result.returncode:
        save(output_dir / "ubinize-report.json", report)
        raise RuntimeError(f"ubinize failed ({result.returncode}): {result.stderr}")
    f = ubi_file(str(output), geometry["peb_size"])
    try:
        container = ubi(f)
        if len(container.images) != 1 or set(container.images[0].volumes) != {"rootdir"}:
            raise ValueError("Unexpected rebuilt UBI volume set")
        image = container.images[0]
        volume = image.volumes["rootdir"]
        if image.image_seq != geometry["image_sequence"] or volume.vol_id != geometry["vol_id"]:
            raise ValueError("UBI identity fields differ")
        for key in ("reserved_pebs", "alignment", "data_pad", "vol_type", "upd_marker", "flags"):
            if getattr(volume.vol_rec, key) != record[key]:
                raise ValueError(f"UBI volume metadata differs: {key}")
        for block in container.blocks.values():
            if (not block.is_valid or block.ec_hdr.vid_hdr_offset != geometry["vid_hdr_offset"]
                    or block.ec_hdr.data_offset != geometry["data_offset"]):
                raise ValueError("Invalid rebuilt UBI headers")
        with candidate.open("rb") as expected:
            for data in volume.reader(container):
                if data != expected.read(len(data)):
                    raise ValueError("Wrapped UBIFS payload differs")
            if expected.read(1):
                raise ValueError("Wrapped UBIFS payload truncated")
        report.update({"passed": True, "output": str(output), "size": output.stat().st_size,
                       "all_ubi_blocks_valid": True, "volume_metadata_equal": True,
                       "ubifs_payload_byte_equal": True, "not_flashable_release": True})
    finally:
        f.close()
    save(output_dir / "ubinize-report.json", report)
    print(json.dumps({k: report[k] for k in ("output", "size", "passed", "ubifs_payload_byte_equal")}, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "build", "verify", "wrap", "stage-linux"])
    for name in ("manifest", "tar", "mkfs", "libdir", "candidate", "log"):
        parser.add_argument("--"+name)
    parser.add_argument("--overlay", type=Path, help="Explicit hib2/desktop/shared scripts + PERSISTENT_HOOKS overlay; separate mod outputs")
    parser.add_argument("--mod", action="store_true", help="Verify or wrap the existing mod candidate")
    args = parser.parse_args()
    if args.action == "inspect":
        inspect()
    elif args.action == "build":
        build(args.overlay)
    elif args.action == "wrap":
        wrap(args.mod)
    elif args.action == "verify":
        output = OUT / "mod" if args.mod else OUT
        candidate = output / ("rootdir-mod-candidate.ubifs" if args.mod else "rootdir-stock-candidate.ubifs")
        metadata = output / "expected-metadata.json" if args.mod else OUT / "stock-metadata.json"
        report = compare(candidate, json.loads(metadata.read_text(encoding="utf-8")), output)
        print(json.dumps({"passed": report["passed"], "mismatch_count": len(report["mismatches"])}, indent=2))
        if not report["passed"]:
            raise SystemExit(1)
    else:
        linux_stage(args)


if __name__ == "__main__":
    main()
