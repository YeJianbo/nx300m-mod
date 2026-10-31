"""Keep host/target Rust metadata and Windows import libraries coherent.

The installed GNU Rust splits full metadata into .rmeta files; explicit lookup
also avoids resolving its metadata-only .rlib stubs when compiling old crates.
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    compiler, *args = sys.argv[1:]
    target = "x86_64-pc-windows-gnu"
    for index, value in enumerate(args):
        if value == "--target" and index + 1 < len(args):
            target = args[index + 1]
        elif value.startswith("--target="):
            target = value.split("=", 1)[1]
    sysroot = Path(os.environ["NX_QS_RUST_SYSROOT"])
    args.extend(["-L", "dependency=" + str(sysroot / "lib/rustlib" / target / "lib")])
    if target == "x86_64-pc-windows-gnu":
        args.extend(["-C", "link-self-contained=yes"])
    return subprocess.run([compiler, *args], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
