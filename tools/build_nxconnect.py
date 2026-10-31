"""Build the AGPL NXConnect adaptation; no camera or device installation."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gradle", type=Path, help="Use an existing Gradle executable instead of the wrapper")
    parser.add_argument("--low-memory", action="store_true", help="Use two workers and a single 768 MiB JVM")
    parser.add_argument("--release", action="store_true", help="Build signed release APK instead of debug")
    parser.add_argument("--srw-check", action="store_true", help="Also run the focused real-SRW metadata checks")
    parser.add_argument("--android-test", action="store_true", help="Also build the Android instrumentation APK")
    args = parser.parse_args()
    project = ROOT / "mod/nxconnect/NXConnect"
    output = ROOT / "outputs/nxconnect"
    output.mkdir(parents=True, exist_ok=True)
    executable = args.gradle or project / ("gradlew.bat" if os.name == "nt" else "gradlew")
    task = ":app:assembleRelease" if args.release else ":app:assembleDebug"
    command = [str(executable.resolve()), task, "--console=plain"]
    if args.android_test:
        command += [":app:assembleDebugAndroidTest"]
    if args.srw_check:
        command += [":app:testReleaseUnitTest" if args.release else ":app:testDebugUnitTest",
                    "--tests", "com.nxwa.nxconnect.ui.SrwExifEditorTest"]
    if args.low_memory:
        command += ["--no-daemon", "--max-workers=2",
                    "-Dorg.gradle.jvmargs=-Xmx768m -XX:ActiveProcessorCount=2 -Dfile.encoding=UTF-8",
                    "-Pkotlin.compiler.execution.strategy=in-process"]
    log_name = "build-release.log" if args.release else "build.log"
    with (output / log_name).open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=project, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
            sys.stdout.flush()
            log.flush()
        result = process.wait()
    if result:
        return result
    flavor = "release" if args.release else "debug"
    apk = project / f"app/build/outputs/apk/{flavor}/app-{flavor}.apk"
    target = output / f"NXConnect-NX300M-{flavor}.apk"
    shutil.copy2(apk, target)
    print(f"APK ({flavor}): {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
