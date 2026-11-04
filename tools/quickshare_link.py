"""Windows rustc -> WSL ARMv7 softfloat musl linker; no camera process access."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from wsl_paths import wsl_path


def linux_path(value: str) -> str:
    if re.match(r"^[A-Za-z]:[\\/]|^[\\/]{2}", value):
        return wsl_path(value, distro=os.environ.get("NX_QS_WSL_DISTRO", "Debian"))
    for prefix in ("-L", "-Wl,--version-script=", "-Wl,-T,"):
        if value.startswith(prefix):
            return prefix + linux_path(value[len(prefix) :])
    return value


def main() -> int:
    args = []
    for value in sys.argv[1:]:
        if value.startswith("@"):
            # GCC response files written by rustc use backslash-escaped tokens.
            content = Path(value[1:]).read_text(encoding="utf-8")
            args.extend(linux_path(x) for x in shlex.split(content))
        else:
            args.append(linux_path(value))
    toolchain = os.environ.get(
        "NX_QS_MUSL_PREFIX", "./local/developer/nx300m-quickshare-toolchain/musl-armv7"
    )
    return subprocess.run(
        [
            "wsl", "-d", os.environ.get("NX_QS_WSL_DISTRO", "Debian"), "--", "env",
            "LD_LIBRARY_PATH=/opt/nx300m-toolchain/usr/lib/x86_64-linux-gnu",
            toolchain + "/bin/musl-gcc", "-march=armv7-a", "-mfloat-abi=soft",
            "-static", *args,
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
