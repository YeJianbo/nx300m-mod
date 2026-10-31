"""Build the optional stock-thread NX300M exposure-control bridge."""
import argparse
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--read-only', action='store_true', help='Build the status-only bridge without exposure writers')
args = parser.parse_args()
compiler = shutil.which('clang')
if not compiler:
    raise SystemExit('clang is required')
output = PROJECT / 'outputs/controls' / ('exposure_status.so' if args.read_only else 'exposure_controls.so')
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([
    compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
    '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
    # Match the shared shim builder: stock glibc 2.13 mishandles DT_RELCOUNT
    # when LLD places .rel.dyn and .rel.plt in separate relocation ranges.
    '-Wl,-z,nocombreloc',
    *(['-DNX_EXPOSURE_READ_ONLY=1'] if args.read_only else []),
    'runtime/exposure_controls.c', '-o', str(output),
], cwd=PROJECT, check=True)
print(output)
