"""Build the source-only native Quick Share prototype using installed tools.

Run using the project's Conda Python. Dependencies are not installed implicitly.
The ARM target requires rustup's armv7-unknown-linux-musleabi standard library
and the separately built ARM musl compiler in WSL.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "mod/quickshare"
OUTPUT = PROJECT / "outputs/quickshare"
TARGET = "armv7-unknown-linux-musleabi"


def wsl_path(path: Path) -> str:
    value = str(path.resolve())
    return "/mnt/" + value[0].lower() + "/" + value[3:].replace("\\", "/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("windows", "arm"), default="windows")
    parser.add_argument("--toolchain", default="stable-x86_64-pc-windows-gnu")
    parser.add_argument("--test-core", action="store_true", help="Run native core regression tests")
    parser.add_argument("--test-filter", help="Run only matching core tests; requires --test-core")
    parser.add_argument("--example", choices=("controlled_receiver",), help="Build a separate test peer, not the installed camera CLI")
    args = parser.parse_args()
    if args.test_core and args.target != "windows":
        parser.error("--test-core runs on the Windows host")
    if args.test_core and args.example:
        parser.error("--test-core and --example are separate build operations")
    if args.test_filter and not args.test_core:
        parser.error("--test-filter requires --test-core")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    descriptor = OUTPUT / "protocol-descriptor.pb"
    proto = SOURCE / "core/src/proto_src"
    files = [
        "device_to_device_messages.proto", "offline_wire_formats.proto",
        "securegcm.proto", "securemessage.proto", "ukey.proto", "wire_format.proto",
    ]
    subprocess.run(
        [
            "wsl", "-d", os.environ.get("NX_QS_WSL_DISTRO", "Debian"), "--", "protoc",
            "--include_imports", "--include_source_info", "-o", wsl_path(descriptor),
            "-I", wsl_path(proto), *[wsl_path(proto / f) for f in files],
        ], check=True,
    )
    env = os.environ.copy()
    env["NX_QS_DESCRIPTOR_SET"] = str(descriptor)
    sysroot = subprocess.check_output(
        ["rustc", "+" + args.toolchain, "--print", "sysroot"], text=True
    ).strip()
    env["NX_QS_RUST_SYSROOT"] = sysroot
    env["NX_QS_PYTHON"] = sys.executable
    env["NX_QS_RUSTC_SCRIPT"] = str(PROJECT / "tools/quickshare_rustc.py")
    env["NX_QS_LINK_SCRIPT"] = str(PROJECT / "tools/quickshare_link.py")
    rustc_wrapper = OUTPUT / "rustc-wrapper.exe"
    helper_source = SOURCE / "tooling/python_launcher.c"
    compiler = shutil.which("x86_64-w64-mingw32-gcc")
    if not compiler:
        raise SystemExit("an installed MinGW gcc is required for the native build helper")
    subprocess.run([compiler, "-O2", "-municode", str(helper_source), "-o", str(rustc_wrapper)], check=True)
    env["RUSTC_WRAPPER"] = str(rustc_wrapper)
    # The local external MinGW is older than Rust's Windows import libraries.
    # Make the toolchain's own imports available to host build-script links too.
    host_imports = str(Path(sysroot) / "lib/rustlib/x86_64-pc-windows-gnu/lib/self-contained")
    env["LIBRARY_PATH"] = host_imports + os.pathsep + env.get("LIBRARY_PATH", "")
    command = ["cargo", "+" + args.toolchain, "build", "--manifest-path", str(SOURCE / "Cargo.toml"), "--release", "--locked"]
    if args.test_core:
        command[2] = "test"
        command.extend(["-p", "rqs_lib", "--lib"])
        if args.test_filter:
            command.append(args.test_filter)
    if args.target == "arm":
        linker = OUTPUT / "link-arm.exe"
        shutil.copy2(rustc_wrapper, linker)
        env["CARGO_TARGET_ARMV7_UNKNOWN_LINUX_MUSLEABI_LINKER"] = str(linker)
        # Kernel feature selection must be visible to Cargo's dependency
        # fingerprints so a previous epoll build is never reused as poll.
        env["CARGO_TARGET_ARMV7_UNKNOWN_LINUX_MUSLEABI_RUSTFLAGS"] = (
            "--cfg mio_unsupported_force_poll_poll "
            "--cfg mio_unsupported_force_waker_pipe "
            "--cfg polling_test_poll_backend"
        )
        command.extend(["--target", TARGET])
        built = SOURCE / "target" / TARGET / "release/nx-quickshare"
        destination = OUTPUT / "nx-quickshare-armv7"
    else:
        built = SOURCE / "target/release/nx-quickshare.exe"
        destination = OUTPUT / "nx-quickshare.exe"
    if args.example:
        command.extend(["--example", args.example])
        suffix = "" if args.target == "arm" else ".exe"
        built = built.parent / "examples" / (args.example + suffix)
        destination = OUTPUT / (args.example + ("-armv7" if args.target == "arm" else ".exe"))
    log = OUTPUT / ("test-core.log" if args.test_core else "build-" + args.target + ".log")
    if args.example:
        log = OUTPUT / ("build-" + args.example + "-" + args.target + ".log")
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=PROJECT, env=env, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f"build failed (exit {result.returncode}); see {log}")
    if args.test_core:
        print(json.dumps({"tests_passed": True, "log": str(log)}))
        return
    shutil.copy2(built, destination)
    print(json.dumps({"artifact": str(destination), "bytes": destination.stat().st_size, "log": str(log)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
