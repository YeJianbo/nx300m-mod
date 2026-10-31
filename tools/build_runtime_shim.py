"""Build the optional ARM hardware-interface substitutes."""

import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
compiler = shutil.which("clang")
if not compiler: raise SystemExit("clang is required")
for name in ('gpio_preview', 'device_preview', 'startup_trace', 'capture_boot', 'ui_mod', 'lut_port', 'ipcm_resize', 'exposure_controls', 'shutter_limit'):
    folder = 'controls' if name in ('exposure_controls', 'shutter_limit') else 'firmware-runtime'
    output = PROJECT / f"outputs/{folder}/{name}.so"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stock glibc 2.13 reuses DT_RELCOUNT across disjoint relocation ranges.
    # LLD can separate .rel.dyn and .rel.plt; suppress that count optimization.
    subprocess.run([compiler, "--target=arm-linux-gnueabi", "-march=armv7-a", "-mfloat-abi=soft", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-nostdlib", "-shared", "-fuse-ld=lld", "-Wl,-z,nocombreloc", f"runtime/{name}.c", "-o", str(output)], cwd=PROJECT, check=True)
    print(output)
