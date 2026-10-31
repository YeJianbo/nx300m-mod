"""Build the native LCD observer without replacing production runtime modules."""
import argparse
from pathlib import Path
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/firmware-runtime/lcd-capture.so'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a',
                    '-mfloat-abi=soft', '-std=c99', '-O2', '-Wall', '-Wextra',
                    '-Werror', '-fPIC', '-ffreestanding', '-fno-builtin',
                    '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
                    '-Wl,-z,nocombreloc',
                    'runtime/lcd_capture.c', '-o', str(args.output)],
                   cwd=PROJECT, check=True)
    print(args.output)


if __name__ == '__main__':
    main()
