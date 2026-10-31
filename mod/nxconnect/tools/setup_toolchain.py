"""Install a self-contained JDK + Android SDK + Gradle into a local directory.

Nothing is installed system-wide; everything lives in one removable directory so the
build is reproducible and fully self-contained.

Where it goes, in priority order:

  1. first command-line argument
  2. the ``NXPATH_HOST`` environment variable
  3. ``_host`` next to the repository root (i.e. ``<repo>/../_host`` when this script
     lives in ``<repo>/tools/``)

Example::

    python tools/setup_toolchain.py            # -> <repo>/_host
    python tools/setup_toolchain.py D:/cache   # -> D:/cache
"""
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(_here)

if len(sys.argv) > 1 and sys.argv[1].strip():
    HOST = os.path.abspath(sys.argv[1].strip())
elif os.environ.get("NXPATH_HOST", "").strip():
    HOST = os.path.abspath(os.environ["NXPATH_HOST"].strip())
else:
    # 脚本在 <repo>/tools/ 下，默认装到 <repo>/_host
    HOST = os.path.join(_repo, "_host")

JDK_DIR = os.path.join(HOST, "jdk")
SDK_DIR = os.path.join(HOST, "android-sdk")
GRADLE_DIR = os.path.join(HOST, "gradle")
DL = os.path.join(HOST, "downloads")
LOG = os.path.join(HOST, "toolchain-log.txt")
os.makedirs(DL, exist_ok=True)
print("toolchain target: %s" % HOST)
lines = []

CTX = ssl.create_default_context()

CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DSH-NX500-setup"}


def log(m):
    m = str(m)
    print(m, flush=True)
    lines.append(m)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def download(url, out, timeout=900):
    if os.path.exists(out) and os.path.getsize(out) > 100000:
        log("cached  %s (%s bytes)" % (out, os.path.getsize(out)))
        return out
    log("GET     %s" % url)
    t0 = time.time()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r, open(out, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 512)
    log("        -> %s bytes in %.1fs" % (os.path.getsize(out), time.time() - t0))
    return out


def run(cmd, cwd=None, env=None, timeout=3600, label=None):
    log("\n$ %s" % (label or " ".join(cmd)))
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                       errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = "\n".join(out.splitlines()[-25:])
    log(tail)
    log("exit=%s" % p.returncode)
    return p.returncode, out


def unzip(path, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)
    log("unzipped %s -> %s" % (os.path.basename(path), dest))


# ---------------------------------------------------------------- 1. JDK 21
def setup_jdk():
    java_exe = os.path.join(JDK_DIR, "bin", "java.exe")
    if os.path.exists(java_exe):
        log("JDK present: %s" % java_exe)
        return java_exe
    urls = [
        "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse",
        "https://mirrors.tuna.tsinghua.edu.cn/Adoptium/21/jdk/x64/windows/OpenJDK21U-jdk_x64_windows_hotspot_21.0.5_11.zip",
        "https://mirrors.aliyun.com/Adoptium/21/jdk/x64/windows/OpenJDK21U-jdk_x64_windows_hotspot_21.0.5_11.zip",
    ]
    out = os.path.join(DL, "jdk21.zip")
    last = None
    for u in urls:
        try:
            download(u, out)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            log("FAIL %s : %s" % (u, e))
    else:
        raise RuntimeError("no JDK mirror worked: %s" % last)
    tmp = os.path.join(HOST, "_jdk_tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    unzip(out, tmp)
    entries = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
    src = os.path.join(tmp, entries[0]) if len(entries) == 1 else tmp
    if os.path.isdir(JDK_DIR):
        shutil.rmtree(JDK_DIR, ignore_errors=True)
    shutil.move(src, JDK_DIR)
    shutil.rmtree(tmp, ignore_errors=True)
    log("JDK installed at %s" % JDK_DIR)
    return java_exe


# ------------------------------------------------------- 2. Android cmdline tools
def setup_cmdline_tools(java_home):
    bat = os.path.join(SDK_DIR, "cmdline-tools", "latest", "bin", "sdkmanager.bat")
    if os.path.exists(bat):
        log("cmdline-tools present")
        return bat
    urls = [
        "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
        "https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip",
    ]
    out = os.path.join(DL, "cmdline-tools.zip")
    last = None
    for u in urls:
        try:
            download(u, out)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            log("FAIL %s : %s" % (u, e))
    else:
        raise RuntimeError("cmdline-tools download failed: %s" % last)
    tmp = os.path.join(HOST, "_cmdline_tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    unzip(out, tmp)
    target_parent = os.path.join(SDK_DIR, "cmdline-tools")
    os.makedirs(target_parent, exist_ok=True)
    latest = os.path.join(target_parent, "latest")
    if os.path.isdir(latest):
        shutil.rmtree(latest, ignore_errors=True)
    shutil.move(os.path.join(tmp, "cmdline-tools"), latest)
    shutil.rmtree(tmp, ignore_errors=True)
    log("cmdline-tools installed at %s" % latest)
    return os.path.join(latest, "bin", "sdkmanager.bat")


# ------------------------------------------------------------------ 3. SDK pkgs
def setup_sdk(java_home, sdkmanager):
    env = dict(os.environ)
    env["JAVA_HOME"] = java_home
    env["ANDROID_HOME"] = SDK_DIR
    env["ANDROID_SDK_ROOT"] = SDK_DIR
    env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")

    pkgs = [
        "platform-tools",
        "platforms;android-35",
        "build-tools;35.0.0",
    ]
    # accept licenses
    try:
        p = subprocess.run([sdkmanager, "--sdk_root=%s" % SDK_DIR, "--licenses"],
                           input="y\n" * 60, capture_output=True, text=True, env=env, timeout=600,
                           errors="replace")
        log("licenses exit=%s" % p.returncode)
    except Exception as e:  # noqa: BLE001
        log("license step failed: %s" % e)
    rc, out = run([sdkmanager, "--sdk_root=%s" % SDK_DIR] + pkgs, env=env, timeout=3600)
    if rc != 0 and "Warning: Failed to read or create install properties" in out:
        log("retrying sdk install after warning")
        run([sdkmanager, "--sdk_root=%s" % SDK_DIR] + pkgs, env=env, timeout=3600)
    return env


# ------------------------------------------------------------------- 4. Gradle
def setup_gradle(java_home):
    gradle_bat = os.path.join(GRADLE_DIR, "bin", "gradle.bat")
    if os.path.exists(gradle_bat):
        log("gradle present")
        return gradle_bat
    urls = [
        "https://services.gradle.org/distributions/gradle-8.11.1-bin.zip",
        "https://mirrors.cloud.tencent.com/gradle/gradle-8.11.1-bin.zip",
        "https://mirrors.aliyun.com/macports/distfiles/gradle/gradle-8.11.1-bin.zip",
    ]
    out = os.path.join(DL, "gradle-8.11.1-bin.zip")
    last = None
    for u in urls:
        try:
            download(u, out)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            log("FAIL %s : %s" % (u, e))
    else:
        raise RuntimeError("gradle download failed: %s" % last)
    tmp = os.path.join(HOST, "_gradle_tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    unzip(out, tmp)
    entries = [d for d in os.listdir(tmp) if d.startswith("gradle")]
    src = os.path.join(tmp, entries[0])
    if os.path.isdir(GRADLE_DIR):
        shutil.rmtree(GRADLE_DIR, ignore_errors=True)
    shutil.move(src, GRADLE_DIR)
    shutil.rmtree(tmp, ignore_errors=True)
    return os.path.join(GRADLE_DIR, "bin", "gradle.bat")


def main():
    log("=== NX500 project toolchain setup ===")
    log("time=%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    java_exe = setup_jdk()
    java_home = os.path.dirname(os.path.dirname(java_exe))
    run([java_exe, "-version"], label="java -version")
    sdkmanager = setup_cmdline_tools(java_home)
    setup_sdk(java_home, sdkmanager)
    gradle_bat = setup_gradle(java_home)
    env = dict(os.environ)
    env["JAVA_HOME"] = java_home
    run([gradle_bat, "--version"], env=env, timeout=1200, label="gradle -v")

    log("\n=== verify sdk layout ===")
    for p in ("platform-tools/adb.exe", "build-tools/35.0.0/aapt2.exe",
              "platforms/android-35/android.jar", "build-tools/35.0.0/apksigner.bat"):
        fp = os.path.join(SDK_DIR, p.replace("/", os.sep))
        log("  %-45s %s" % (p, "OK" if os.path.exists(fp) else "MISSING"))

    with open(os.path.join(HOST, "toolchain-paths.txt"), "w", encoding="utf-8") as f:
        f.write("JAVA_HOME=%s\n" % java_home)
        f.write("JAVA=%s\n" % java_exe)
        f.write("ANDROID_HOME=%s\n" % SDK_DIR)
        f.write("GRADLE=%s\n" % gradle_bat)
        f.write("ADB=%s\n" % os.path.join(SDK_DIR, "platform-tools", "adb.exe"))
    log("\nDONE - paths in %s" % os.path.join(HOST, "toolchain-paths.txt"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("SETUP FAILED: %s: %s" % (type(e).__name__, e))
        raise
