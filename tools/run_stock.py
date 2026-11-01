"""Run genuine stock ARM programs in a private WSL chroot with no camera devices."""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from mfw_runtime import firmware_profile, overlay_setup, LIBRARY as MFW_LIBRARY

PROJECT = Path(__file__).resolve().parents[1]
GUEST = "/opt/nx300m-lab/stock-1.15"


def diagnostic_sample_source(directory):
    """Record selected external files without asserting they came from ISP."""
    if not directory:
        return None
    guest_path = PurePosixPath(directory)
    if not guest_path.is_absolute() or '..' in guest_path.parts:
        raise ValueError('Diagnostic sample directory must be an absolute guest path without ..')
    source = Path('//wsl.localhost/Debian'+GUEST)/str(guest_path).lstrip('/')
    if not source.is_dir():
        raise ValueError(f'Diagnostic sample directory does not exist: {directory}')
    files = []
    for path in sorted(source.glob('sample_*.bin')):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append({'name':path.name,'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns})
    if not files:
        raise ValueError(f'Diagnostic sample directory contains no sample_*.bin files: {directory}')
    return {'guest_directory':str(guest_path),'source_kind':'external_ycbcr_samples',
            'files':files,'metadata_observed_utc':datetime.now(timezone.utc).isoformat(),
            'note':'Pre-launch file metadata; encoder validates exact plane lengths at use. Not ISP pixels.'}


def wsl_path(path):
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


def shell(script, **kwargs):
    return subprocess.run(["wsl.exe", "-u", "root", "-e", "sh", "-c", script], **kwargs)


def save_runs(output, summary, program):
    summary_path = output / "runs.json"
    if summary_path.exists() and program != "all":
        previous = json.loads(summary_path.read_text(encoding="utf-8"))
        previous["runs"].update(summary["runs"])
        summary["runs"] = previous["runs"]
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def prepare(keep_qemu=False):
    source = PROJECT / "work/stock-1.15/source.json"
    if not source.exists(): raise RuntimeError("Run prepare_stock.py first")
    provenance = json.loads(source.read_text(encoding="utf-8"))
    archive = PROJECT / "work/stock-1.15/rootfs.tar"
    qemu = PROJECT / "work/qemu/qemu-arm-static"
    if not archive.is_file() or not qemu.is_file(): raise RuntimeError("rootfs.tar or qemu-arm-static is missing")
    marker = provenance["local_binary_sha256"]
    qemu_setup = f'''cp {shlex.quote(wsl_path(qemu))} "$guest/qemu-arm-static.$$"
chmod 755 "$guest/qemu-arm-static.$$"
mv "$guest/qemu-arm-static.$$" "$guest/qemu-arm-static"
''' if not keep_qemu else 'test -x "$guest/qemu-arm-static"\n'
    script = f"""set -eu
guest={shlex.quote(GUEST)}
if [ -f "$guest/.stock-source" ]; then
    [ "$(cat "$guest/.stock-source")" = {shlex.quote(marker)} ] || exit 2
else
    [ ! -e "$guest" ] || {{ echo 'Guest directory already exists without source marker' >&2; exit 2; }}
    mkdir -p "$guest"
    tar -xf {shlex.quote(wsl_path(archive))} -C "$guest" --no-same-owner
    printf '%s' {shlex.quote(marker)} > "$guest/.stock-source"
fi
{qemu_setup}
mkdir -p "$guest/tmp" "$guest/dev" "$guest/run"
chmod 1777 "$guest/tmp"
for spec in 'null 1 3' 'zero 1 5' 'random 1 8' 'urandom 1 9'; do
    set -- $spec
    [ -e "$guest/dev/$1" ] || mknod "$guest/dev/$1" c "$2" "$3"
    chmod 666 "$guest/dev/$1"
done
"""
    shell(script, check=True)
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument('--firmware', choices=('stock', 'mfw'), default='stock',
                        help='mfw: privately overlay the verified camera-exported script-entry library')
    parser.add_argument('--qemu', help='Bind this alternate host QEMU only inside the private camera mount namespace')
    parser.add_argument("--program", choices=("all", "busybox", "st", "prefman", "prefman-info", "st-cap", "camera"), default="all")
    parser.add_argument("--display", action="store_true", help="Provide a private Xvfb display for the original application")
    parser.add_argument("--screen", choices=('480x800', '800x480'), default='480x800', help='Raw X11 screen geometry; stock UI uses a rotated portrait panel')
    parser.add_argument("--core", action="store_true", help="Allow a target core dump in the disposable guest root")
    parser.add_argument("--preferences", action="store_true", help="Initialize private IPC preferences with the original utility")
    parser.add_argument("--gpio-preview", action="store_true", help="Use the explicitly synthetic GPIO register substitute")
    parser.add_argument("--device-preview", action="store_true", help="Use synthetic PWM/SPI registers and SMA/BMA memory backends")
    parser.add_argument("--shutter-preview", action="store_true", help="Enable the experimental Allegro timer/explicit PI2 backend; requires GPIO/device previews")
    parser.add_argument("--isp-preview", action="store_true", help="Enable experimental PP/IPCM/IPCS register transport; no sensor/DMA completion")
    parser.add_argument('--cpu-ycc-resize', action='store_true', help='Execute uncompressed IPCM combi YCC resize using a software bilinear filter')
    parser.add_argument('--cpu-jpeg-decode', action='store_true', help='Decode actual playback JPEG bytes with stock libjpeg in software')
    parser.add_argument('--jpeg-task-trace', action='store_true', help='Observe stock JPEG encode tasks and requested ranges; preserve original encoder')
    parser.add_argument('--jpeg-cpu-diagnostic', action='store_true', help='Independent lab only: encode unverified input snapshots to expose subsequent stock calls')
    parser.add_argument('--diagnostic-sample-dir', help='Absolute guest directory of complete external Y/CbCr planes; requires --jpeg-cpu-diagnostic')
    parser.add_argument('--software-isp-source', type=Path, help='Explicit same-RAW source.bin from prepare_software_isp.py; software Bayer model on an independent card')
    parser.add_argument('--cpu-playback', action='store_true', help='Execute playback resize and YCC-to-RGB operations in software')
    parser.add_argument('--replay-packet', type=Path, help='Start an explicit prepared IPCM image replay as soon as the original stream is ready')
    parser.add_argument('--replay-continuous', action='store_true', help='Keep feeding recorded frames until the private camera session stops')
    parser.add_argument('--sdcard', action='store_true', help='Attach the persistent 1 GiB lab FAT32 card and explicit card-detect input')
    parser.add_argument('--sdcard-image', help='Absolute WSL path to a separate lab card image; requires --sdcard')
    parser.add_argument('--spi-mmio', action='store_true', help='Execute stock A32 SPI accesses through the FIFO controller model; no sensor peer')
    parser.add_argument('--pwm-timing', action='store_true', help='Finite PWM counters from original register writes; explicit external trigger FIFO, lab 7.5 ns clock')
    parser.add_argument('--bayer-replay', type=Path, help='Explicit prepared Bayer packet at the PP WDMA boundary, no sensor/PP pixel processing')
    parser.add_argument('--sensor-registers', action='store_true', help='Model Aquila written registers on the original board SPI channel; no frames')
    parser.add_argument('--d4c-registers', action='store_true', help='Model D4C written registers and retain opaque burst payloads; no DSP/frame output')
    parser.add_argument("--shutter-park", choices=('10', '01'), help='Explicit cold PI1/PI2 input levels for the shutter scenario')
    parser.add_argument('--hotshoe-input', choices=('absent', 'unmodeled'), default='absent', help='Explicit empty-hotshoe GPIO input for the simulator; no accessory communications')
    parser.add_argument('--shutter-motion', choices=('observe', 'ideal-8-sector'), help='Observe motor GPIOs or enable an uncalibrated ideal rotor; no capture callbacks')
    parser.add_argument('--shutter-step-us', type=int, default=10000, help='Ideal rotor sector duration, 3000–1000000 us')
    parser.add_argument("--trace-startup", action="store_true", help="Log and forward original startup and liveview callback calls")
    parser.add_argument('--capture-geometry', action='store_true', help='Record original Bayer split geometry and boundary bytes with the dedicated trace build')
    parser.add_argument("--ui-mod", action="store_true", help="Load the stock Ecore key bridge and command-dial lock")
    exposure_mode = parser.add_mutually_exclusive_group()
    exposure_mode.add_argument('--exposure-controls', action='store_true', help='Load the original UserData ISO preference bridge on the stock Ecore thread')
    exposure_mode.add_argument('--exposure-read-only', action='store_true', help='Load only the status bridge; reject exposure writes and avoid creating stock objects')
    parser.add_argument('--shutter-limit', action='store_true', help='Load the capture AE fastest-shutter bound; disabled until configured')
    parser.add_argument("--lut-port", action="store_true", help="Forward custom LUT data at original stock LUT callbacks")
    parser.add_argument('--color-style', action='store_true', help='Load independent RGB output-row gains; neutral until configured')
    parser.add_argument('--service-loader', action='store_true', help='Watch the configured mod root and asynchronously trigger its fixed start.sh')
    parser.add_argument('--gallery-share', action='store_true', help='Open the native Quick Share panel from the original single-photo playback share action')
    parser.add_argument('--gallery-photo', type=Path, help='Seed only this explicit JPEG on the private gallery card')
    parser.add_argument('--isp-color-observer', action='store_true', help='Record original Gamma/CCM setter parameters without changing their values')
    parser.add_argument('--capture-observer', action='store_true', help='Observe original file-write results and backend callbacks without declaring photo success')
    parser.add_argument('--firmware-mod', action='store_true', help='Opt in to the camera firmware-mode RPC module; camera only, disabled by default')
    parser.add_argument('--firmware-entry-diagnostics', action='store_true', help='Opt in to firmware entry op8 diagnostics; requires --firmware-mod')
    parser.add_argument('--manual-lens-wifi', action='store_true', help='Opt in to verified NX300M no-attached-lens Wi-Fi admission; requires --firmware-mod')
    parser.add_argument('--manual-lens-cs', action='store_true', help='Opt in to experimental electronic-lens 2D CS guards/default rebuild; camera and independent output directory required')
    parser.add_argument('--lcd-capture', action='store_true', help='Observe accepted LCD ioctls; cannot share output with the device preview backend')
    parser.add_argument("--keep-awake", action="store_true", help="Disable stock power saving in private simulator preferences")
    parser.add_argument("--capture-boot", action="store_true", help="Explicitly select stock capture memory mode before first liveview lookup")
    parser.add_argument("--mmap-script-fix", action="store_true", help="Privately correct the duplicate playback client in the stock NX300M memory script")
    parser.add_argument("--seconds", type=int, default=12, help="Observation duration, 1–600 seconds")
    parser.add_argument("--no-strace", action="store_true", help="Disable syscall tracing for an interactive session")
    parser.add_argument("--tag", default='', help="Short label to preserve a distinct configuration/run")
    parser.add_argument('--output-dir', type=Path, default=PROJECT / 'outputs/firmware-runtime', help='Separate session/log/framebuffer directory for parallel development')
    args = parser.parse_args()
    if args.firmware == 'mfw' and args.program != 'camera':
        parser.error('--firmware mfw requires --program camera')
    firmware = firmware_profile(args.firmware, prepare=True)
    if args.firmware_entry_diagnostics and not args.firmware_mod:
        parser.error('--firmware-entry-diagnostics requires --firmware-mod')
    if args.manual_lens_wifi and not args.firmware_mod:
        parser.error('--manual-lens-wifi requires --firmware-mod')
    if args.manual_lens_cs and (args.program != 'camera' or
            args.output_dir.resolve() == (PROJECT/'outputs/firmware-runtime').resolve()):
        parser.error('--manual-lens-cs requires camera and an independent --output-dir')
    if args.software_isp_source:
        if (args.program!='camera' or not args.bayer_replay or not args.device_preview or
                args.output_dir.resolve()==(PROJECT/'outputs/firmware-runtime').resolve()):
            parser.error('--software-isp-source requires camera, Bayer replay, device preview and an independent output directory')
        if args.jpeg_cpu_diagnostic:
            parser.error('--software-isp-source and --jpeg-cpu-diagnostic are separate execution modes')
        if not args.sdcard or args.sdcard_image!='/opt/nx300m-lab/media/nx300m-software-isp.img':
            parser.error('--software-isp-source requires the dedicated /opt/nx300m-lab/media/nx300m-software-isp.img card')
        if not args.software_isp_source.is_file(): parser.error('Software ISP source config does not exist')
        args.jpeg_task_trace=True
    if args.diagnostic_sample_dir and not args.jpeg_cpu_diagnostic:
        parser.error('--diagnostic-sample-dir requires --jpeg-cpu-diagnostic')
    if args.jpeg_cpu_diagnostic:
        args.diagnostic_sample_dir = args.diagnostic_sample_dir or os.environ.get('NX300M_DIAGNOSTIC_SAMPLE_DIR')
    try:
        sample_source = diagnostic_sample_source(args.diagnostic_sample_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.jpeg_cpu_diagnostic:
        if (args.program != 'camera' or not args.device_preview or
                args.output_dir.resolve() == (PROJECT/'outputs/firmware-runtime').resolve()):
            parser.error('--jpeg-cpu-diagnostic requires a camera device backend and independent --output-dir')
        if not args.sdcard or args.sdcard_image != '/opt/nx300m-lab/media/nx300m-jpeg-diagnostic.img':
            parser.error('--jpeg-cpu-diagnostic requires --sdcard --sdcard-image /opt/nx300m-lab/media/nx300m-jpeg-diagnostic.img')
        args.jpeg_task_trace = True
    if args.jpeg_task_trace and args.program != 'camera':
        parser.error('--jpeg-task-trace requires --program camera')
    if args.isp_color_observer and args.program != 'camera':
        parser.error('--isp-color-observer requires --program camera')
    if args.capture_observer and args.program != 'camera':
        parser.error('--capture-observer requires --program camera')
    if args.firmware_mod and args.program != 'camera':
        parser.error('--firmware-mod requires --program camera')
    if (args.cpu_jpeg_decode or args.cpu_playback) and (args.program != 'camera' or not args.device_preview):
        parser.error('--cpu-jpeg-decode and --cpu-playback require --program camera --device-preview')
    if args.capture_geometry:
        if args.program != 'camera': parser.error('--capture-geometry requires --program camera')
        args.trace_startup = True
    if args.sdcard_image and (not args.sdcard or not args.sdcard_image.startswith('/')):
        parser.error('--sdcard-image requires --sdcard and an absolute WSL path')
    if args.lcd_capture and args.device_preview:
        parser.error('--lcd-capture cannot be combined with --device-preview (both publish LCD state)')
    if (args.color_style or args.lcd_capture or args.service_loader or args.gallery_share) and args.program != 'camera':
        parser.error('--color-style, --lcd-capture, --service-loader and --gallery-share require --program camera')
    if args.shutter_preview and not (args.gpio_preview and args.device_preview):
        parser.error('--shutter-preview requires --gpio-preview and --device-preview')
    if args.pwm_timing and not args.device_preview:
        parser.error('--pwm-timing requires --device-preview')
    if args.bayer_replay:
        if args.program != 'camera' or not args.isp_preview:
            parser.error('--bayer-replay requires --program camera --isp-preview')
        from replay_bayer import inspect_packet
        inspect_packet(args.bayer_replay)
    if args.isp_preview and not args.device_preview:
        parser.error('--isp-preview requires --device-preview')
    if args.cpu_ycc_resize and not args.isp_preview:
        parser.error('--cpu-ycc-resize requires --isp-preview')
    if args.replay_packet and (args.program != 'camera' or not args.cpu_ycc_resize):
        parser.error('--replay-packet requires --program camera --cpu-ycc-resize')
    if args.replay_continuous and not args.replay_packet:
        parser.error('--replay-continuous requires --replay-packet')
    if args.sdcard and (args.program != 'camera' or not args.preferences):
        parser.error('--sdcard requires --program camera --preferences')
    if args.spi_mmio and not (args.gpio_preview and args.device_preview):
        parser.error('--spi-mmio requires --gpio-preview and --device-preview')
    if args.sensor_registers and not args.spi_mmio:
        parser.error('--sensor-registers requires --spi-mmio')
    if args.d4c_registers and not args.spi_mmio:
        parser.error('--d4c-registers requires --spi-mmio')
    if args.shutter_park and not args.shutter_preview:
        parser.error('--shutter-park requires --shutter-preview')
    if args.shutter_motion and not args.shutter_preview:
        parser.error('--shutter-motion requires --shutter-preview')
    if not 3000 <= args.shutter_step_us <= 1000000:
        parser.error('--shutter-step-us must be between 3000 and 1000000')
    if args.shutter_motion == 'ideal-8-sector' and not args.shutter_park:
        parser.error('--shutter-motion ideal-8-sector requires --shutter-park')
    if not 0 <= args.seconds <= 600: parser.error("--seconds must be between 0 and 600 (0 means until stopped)")
    if args.tag and not re.fullmatch(r'[a-zA-Z0-9_-]+', args.tag): parser.error('Invalid --tag')
    if args.qemu and args.program!='camera':
        parser.error('--qemu currently applies only to the private camera session')
    qemu_override = args.qemu if args.qemu and args.qemu.startswith('/') else wsl_path(Path(args.qemu).resolve()) if args.qemu else None
    if qemu_override:
        shell('test -x '+shlex.quote(qemu_override), check=True)
    provenance = prepare(keep_qemu=bool(qemu_override))
    print(f"Prepared stock guest: {GUEST}", flush=True)
    if args.prepare_only: return
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    commands = {
        "busybox":["/bin/busybox", "echo", "stock NX300M ARM busybox executed"],
        "st":["/usr/bin/st"],
        "prefman":["/usr/bin/prefman", "--help"],
        "prefman-info":["/usr/bin/prefman", "info", "1"],
        "st-cap":["/usr/bin/st", "cap"],
        "camera":["/usr/bin/di-camera-app-nx300"],
    }
    qemu_source_file = PROJECT / 'work/qemu/SOURCE.json'
    qemu_metadata = json.loads(qemu_source_file.read_text(encoding='utf-8')) if qemu_source_file.is_file() else {}
    qemu_source = qemu_override or qemu_metadata.get('package_url') or qemu_metadata.get('asset') or str(PROJECT / 'work/qemu/qemu-arm-static')
    summary = {"model":"NX300M", "version":"1.15", "binary_sha256":provenance["local_binary_sha256"],
               "firmware":firmware,
               "qemu_source":qemu_source, "execution":"unmodified stock ARM executables, user-mode QEMU, private chroot, unprivileged UID, isolated network", "runs":{}}
    for name, argv in commands.items():
        if args.program not in ("all", name): continue
        core_root = Path("//wsl.localhost/Debian" + GUEST)
        old_cores = set(core_root.glob("qemu_*.core")) if args.core else set()
        guest_command = ["/qemu-arm-static", "-cpu", "cortex-a9", "-L", "/", "-strace", *argv]
        if args.no_strace: guest_command.remove('-strace')
        run_name = name + ("-xvfb" if name == "camera" and args.display else "") + ("-prefs" if name == "camera" and args.preferences else "") + ("-gpio" if name == "camera" and args.gpio_preview else "")
        if name == 'camera' and args.device_preview: run_name += '-devices'
        if name == 'camera' and args.shutter_preview: run_name += '-shutter'
        if name == 'camera' and args.isp_preview: run_name += '-isp'
        if name == 'camera' and args.cpu_ycc_resize: run_name += '-cpursz'
        if name == 'camera' and args.cpu_jpeg_decode: run_name += '-cpujpeg'
        if name == 'camera' and args.cpu_playback: run_name += '-cpupb'
        if name == 'camera' and args.spi_mmio: run_name += '-spimmio'
        if name == 'camera' and args.sensor_registers: run_name += '-cisregs'
        if name == 'camera' and args.d4c_registers: run_name += '-d4cregs'
        if name == 'camera' and args.trace_startup: run_name += '-trace'
        if name == 'camera' and args.capture_geometry: run_name += '-geometry'
        if name == 'camera' and args.capture_boot: run_name += '-capture'
        if name == 'camera' and args.mmap_script_fix: run_name += '-scriptfix'
        if name == 'camera' and args.lut_port: run_name += '-lut'
        if name == 'camera' and args.exposure_controls: run_name += '-exposure'
        if name == 'camera' and args.exposure_read_only: run_name += '-exposure-readonly'
        if name == 'camera' and args.shutter_limit: run_name += '-shutterlimit'
        if name == 'camera' and args.color_style: run_name += '-color'
        if name == 'camera' and args.service_loader: run_name += '-services'
        if name == 'camera' and args.gallery_share: run_name += '-galleryshare'
        if name == 'camera' and args.isp_color_observer: run_name += '-colorobserve'
        if name == 'camera' and args.capture_observer: run_name += '-captureobserve'
        if name == 'camera' and args.manual_lens_cs: run_name += '-manual-lens-cs'
        if name == 'camera' and args.lcd_capture: run_name += '-lcdcapture'
        if args.tag: run_name += '-' + args.tag
        log_name = run_name + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        stdout_path = output / f'{log_name}.stdout.log'
        stderr_path = output / f'{log_name}.strace.log'
        preloads = []
        shim_setup = ''
        if name == 'camera' and args.capture_observer:
            observer = PROJECT / 'outputs/firmware-runtime/capture-observer.so'
            if not observer.is_file():
                raise RuntimeError('Run tools/build_capture_observer.py first')
            target = GUEST + '/tmp/capture-observer.so'
            shim_setup += f"cp {shlex.quote(wsl_path(observer))} {shlex.quote(target)} || exit 8;\n"
            preloads.append('/tmp/capture-observer.so')
        if name == 'camera' and args.isp_color_observer:
            observer = PROJECT / 'outputs/firmware-runtime/isp-color-observer.so'
            if not observer.is_file():
                raise RuntimeError('Run tools/build_isp_color_observer.py first')
            target = GUEST + '/tmp/isp-color-observer.so'
            shim_setup += f"cp {shlex.quote(wsl_path(observer))} {shlex.quote(target)} && chmod 755 {shlex.quote(target)} || exit 8;\n"
            preloads.append('/tmp/isp-color-observer.so')
        for enabled, shim_name in ((args.gpio_preview, 'gpio_preview'), (args.device_preview, 'device_preview'), (args.trace_startup, 'startup_trace'), (args.capture_boot, 'capture_boot'), (args.ui_mod, 'ui_mod'), (args.lut_port, 'lut_port'), (args.cpu_ycc_resize, 'ipcm_resize'), (args.exposure_controls, 'exposure_controls'), (args.exposure_read_only, 'exposure_status'), (args.shutter_limit, 'shutter_limit'), (args.color_style, 'color-style'), (args.lcd_capture, 'lcd-capture'), (args.service_loader, 'service-loader'), (args.gallery_share, 'gallery-share'), (args.firmware_mod, 'camera_firmware_mod')):
            if name != 'camera' or not enabled: continue
            folder = PROJECT / 'outputs/controls' if shim_name in ('exposure_controls', 'exposure_status', 'shutter_limit', 'camera_firmware_mod') else PROJECT / 'outputs/metol' if shim_name == 'color-style' else PROJECT / 'outputs/firmware-runtime'
            shim = folder / f"{shim_name}.so"
            if shim_name == 'service-loader': shim = PROJECT / 'outputs/deployment/service-loader/service-loader.so'
            if shim_name == 'gallery-share': shim = PROJECT / 'outputs/quickshare/gallery/gallery-share.so'
            if shim_name == 'startup_trace' and args.capture_geometry:
                shim = PROJECT / 'outputs/firmware-runtime/startup_trace_geometry.so'
            if shim_name == 'camera_firmware_mod' and not shim.is_file():
                subprocess.run([sys.executable,str(PROJECT/'tools/build_camera_firmware_mod.py')],cwd=PROJECT,check=True)
            if not shim.is_file():
                if shim_name == 'exposure_status':
                    raise RuntimeError('Run tools/build_exposure_controls.py --read-only first')
                builder = 'build_gallery_share.py' if shim_name == 'gallery-share' else 'build_service_loader.py' if shim_name == 'service-loader' else 'build_capture_geometry_trace.py' if shim_name == 'startup_trace' and args.capture_geometry else 'build_color_style.py' if shim_name == 'color-style' else 'build_lcd_capture.py' if shim_name == 'lcd-capture' else 'build_runtime_shim.py'
                raise RuntimeError(f"Run tools/{builder} first")
            target = GUEST + f'/tmp/{shim_name}.so'
            shim_setup += f"cp {shlex.quote(wsl_path(shim))} {shlex.quote(target)} && chmod 755 {shlex.quote(target)} || exit 8;\n"
            preloads.append(f'/tmp/{shim_name}.so')
        for enabled, module, builder in (
                (args.jpeg_task_trace, 'jpeg-encode', 'build_jpeg_encode.py'),
                (args.cpu_jpeg_decode, 'jpeg-decode', 'build_jpeg_decode.py'),
                (args.cpu_playback, 'playback-pixels', 'build_playback_pixels.py')):
            if name != 'camera' or not enabled: continue
            shim = PROJECT/'outputs/firmware-runtime'/module/(module+'.so')
            if not shim.is_file(): raise RuntimeError(f'Run tools/{builder} first')
            target = GUEST+'/tmp/'+module+'.so'
            shim_setup += f"cp {shlex.quote(wsl_path(shim))} {shlex.quote(target)} && chmod 755 {shlex.quote(target)} || exit 8;\n"
            preloads.append('/tmp/'+module+'.so')
        if name == 'camera' and args.manual_lens_cs:
            shim = PROJECT/'outputs/controls/manual-lens/manual-lens-cs.so'
            if not shim.is_file():
                raise RuntimeError('Run tools/build_manual_lens_cs.py first')
            target = GUEST+'/tmp/manual-lens-cs.so'
            shim_setup += f"cp {shlex.quote(wsl_path(shim))} {shlex.quote(target)} && chmod 755 {shlex.quote(target)} || exit 8;\n"
            preloads.append('/tmp/manual-lens-cs.so')
            guest_command[1:1] = ['-E', 'NX300M_MANUAL_LENS_CS=1']
        if preloads: guest_command[1:1] = ['-E', 'LD_PRELOAD=' + ':'.join(preloads)]
        if args.jpeg_task_trace and name == 'camera':
            guest_command[1:1] = ['-E', 'NX300M_JPEG_TASK_TRACE=1', '-E', 'NX300M_CPU_JPEG=0']
        if args.jpeg_cpu_diagnostic and name == 'camera':
            guest_command[1:1] = ['-E', 'NX300M_JPEG_DIAGNOSTIC=1', '-E', 'NX300M_JPEG_DIAGNOSTIC_ISOLATED=1']
            sample_dir = args.diagnostic_sample_dir
            if sample_dir:
                guest_command[1:1] = ['-E', f'NX300M_DIAGNOSTIC_SAMPLE_DIR={sample_dir}']
        if args.software_isp_source and name=='camera':
            target=GUEST+'/tmp/software-isp-source.bin'
            shim_setup+='cp '+shlex.quote(wsl_path(args.software_isp_source.resolve()))+' '+shlex.quote(target)+' && chmod 644 '+shlex.quote(target)+' || exit 8;\n'
            guest_command[1:1]=['-E','NX300M_SOFTWARE_ISP_SOURCE=/tmp/software-isp-source.bin']
        if args.firmware_entry_diagnostics and name=='camera':
            guest_command[1:1]=['-E','NX300M_FIRMWARE_ENTRY_DIAGNOSTICS=1']
        if args.manual_lens_wifi and name=='camera':
            guest_command[1:1]=['-E','NX300M_MANUAL_LENS_WIFI=1']
        if args.capture_geometry and name == 'camera': guest_command[1:1] = ['-E', 'NX300M_CAPTURE_GEOMETRY=1']
        if args.shutter_preview and name == 'camera': guest_command[1:1] = ['-E', 'NX300M_SHUTTER_SIM=1']
        if args.gpio_preview and name == 'camera' and args.hotshoe_input == 'absent':
            guest_command[1:1] = ['-E', 'NX300M_HOTSHOE_ABSENT=1']
        if args.isp_preview and name == 'camera': guest_command[1:1] = ['-E', 'NX300M_ISP_SIM=1']
        if args.spi_mmio and name == 'camera':
            guest_command[1:1] = ['-E', 'NX300M_SPI_MMIO=1', '-E', 'NX300M_SPI_PEER='+('aquila-registers' if args.sensor_registers else 'none')]
        if args.pwm_timing and name == 'camera':
            guest_command[1:1] = ['-E', 'NX300M_PWM_TIMING=1']
        if args.bayer_replay and name == 'camera':
            packet_target = GUEST+'/tmp/pp-recorded-bayer.bin'
            shim_setup += 'cp '+shlex.quote(wsl_path(args.bayer_replay.resolve()))+' '+shlex.quote(packet_target)+' && chmod 644 '+shlex.quote(packet_target)+' || exit 8;\n'
            guest_command[1:1] = ['-E', 'NX300M_PP_BAYER_REPLAY=/tmp/pp-recorded-bayer.bin']
        if args.d4c_registers and name == 'camera':
            guest_command[1:1] = ['-E', 'NX300M_D4C_REGISTERS=1']
        if args.shutter_park and name == 'camera': guest_command[1:1] = ['-E', 'NX300M_SHUTTER_PARK='+args.shutter_park]
        if args.shutter_motion and name == 'camera':
            guest_command[1:1] = ['-E', 'NX300M_SHUTTER_MOTION='+args.shutter_motion, '-E', 'NX300M_SHUTTER_STEP_US='+str(args.shutter_step_us)]
        quoted = " ".join(shlex.quote(value) for value in guest_command)
        if name == "camera":
            temporary_setup = f'mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {shlex.quote(GUEST + "/tmp")} || exit 8;\n'
            if qemu_override:
                temporary_setup = ('mount --make-rprivate / || exit 8;\nmount --bind '+
                                   shlex.quote(qemu_override)+' '+shlex.quote(GUEST+'/qemu-arm-static')+
                                   ' || exit 8;\n')+temporary_setup
            display_setup = ""
            if args.display:
                framebuffer = output / "framebuffer"
                framebuffer.mkdir(exist_ok=True)
                display_setup = f"""set -e
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp
mkdir -m 1777 /tmp/.X11-unix
mkdir -p {shlex.quote(GUEST + '/tmp/.X11-unix')}
Xvfb :99 -screen 0 {args.screen}x24 -s 0 -nolisten tcp -ac -fbdir {shlex.quote(wsl_path(framebuffer))} >{shlex.quote(wsl_path(output / 'xvfb.log'))} 2>&1 &
ready=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if [ -S /tmp/.X11-unix/X99 ]; then ready=1; break; fi
    sleep 0.1
done
[ "$ready" = 1 ] || exit 3
touch {shlex.quote(GUEST + '/tmp/.X11-unix/X99')}
mount --bind /tmp/.X11-unix/X99 {shlex.quote(GUEST + '/tmp/.X11-unix/X99')}
export DISPLAY=:99
set +e
"""
            preference_setup = ""
            if args.preferences:
                preference_setup = f"sh {shlex.quote(wsl_path(PROJECT / 'runtime/bootstrap_preferences.sh'))} {shlex.quote(GUEST)} || exit 4;\n"
                if args.keep_awake:
                    for offset in ('0x300', '0x304'):
                        preference_setup += f'chroot --userspec=65534:65534 {shlex.quote(GUEST)} /qemu-arm-static /usr/bin/prefman set 1 {offset} l 1 || exit 4;\n'
                    preference_setup += 'echo SIMULATOR_DISABLE_DISPLAY_AND_POWER_SAVE=1 >&2;\n'
            elif args.keep_awake:
                raise ValueError('--keep-awake requires --preferences')
            override_setup = ''
            if args.firmware == 'mfw':
                override_setup += overlay_setup(GUEST, wsl_path(MFW_LIBRARY))
            if args.mmap_script_fix:
                relative = '/usr/lib/sys-mmap-script/sysmmap-script-NX300M'
                original_script = (core_root / relative.lstrip('/')).read_bytes()
                patched_script, changes = re.subn(rb'(//Define Playback Edit Map\r?\nCLIENT srp )playback( n 0 1)', rb'\g<1>pbedit\2', original_script)
                if changes != 1: raise RuntimeError('Expected exactly one stock playback-edit client typo')
                override = PROJECT / 'work/runtime/sysmmap-script-NX300M'
                override.parent.mkdir(parents=True, exist_ok=True)
                override.write_bytes(patched_script)
                # Copy onto ext4 first: old 32-bit stock stat() overflows on
                # the 64-bit inode numbers exported by the Windows mount.
                guest_override = GUEST + '/tmp/sysmmap-script-override'
                override_setup += f'cp {shlex.quote(wsl_path(override))} {shlex.quote(guest_override)} && chmod 644 {shlex.quote(guest_override)} && mount --bind {shlex.quote(guest_override)} {shlex.quote(GUEST + relative)} || exit 5; echo MMAP_PRIVATE_SCRIPT_FIX=1 >&2;\n'
            proc_setup = f'mkdir -p {shlex.quote(GUEST + "/proc")}; mount -t proc -o nosuid,nodev,noexec proc {shlex.quote(GUEST + "/proc")} || exit 6;\n'
            card_setup = ''
            if args.sdcard:
                image_option = ' --image '+shlex.quote(args.sdcard_image) if args.sdcard_image else ''
                card_setup = f'python3 {shlex.quote(wsl_path(PROJECT/"tools/sdcard.py"))} --bootstrap {shlex.quote(GUEST)}{image_option} || exit 7;\n'
                if args.gallery_share:
                    photo_option = ' --photo '+shlex.quote(wsl_path(args.gallery_photo.resolve())) if args.gallery_photo else ''
                    card_setup += f'python3 {shlex.quote(wsl_path(PROJECT/"tools/stock_gallery_runtime.py"))} --bootstrap {shlex.quote(GUEST)}{photo_option} || exit 9;\n'
            state_path = output / 'session.json'
            session_format = json.dumps({'namespace_pid': -1, 'raw_screen': args.screen,
                                         'firmware':firmware,
                                         'stdout_log': stdout_path.name, 'stderr_log': stderr_path.name,
                                         'cpu_jpeg_decode':args.cpu_jpeg_decode,'cpu_playback':args.cpu_playback,
                                         'jpeg_task_trace':args.jpeg_task_trace,
                                         'jpeg_cpu_diagnostic':args.jpeg_cpu_diagnostic,
                                         'firmware_mod':args.firmware_mod,
                                         'firmware_entry_diagnostics':args.firmware_entry_diagnostics,
                                         'manual_lens_wifi':args.manual_lens_wifi,
                                         'manual_lens_cs':args.manual_lens_cs,
                                         'software_isp_source':args.software_isp_source.resolve().as_posix() if args.software_isp_source else None,
                                         'diagnostic_sample_source':sample_source,
                                         'gallery_share':args.gallery_share,'sdcard':args.sdcard,
                                         'exposure_read_only':args.exposure_read_only,
                                         'isp_color_observer':args.isp_color_observer,'capture_observer':args.capture_observer,
                                         'media_root':'/mnt/mmc/DCIM' if args.sdcard else None})
            session_format = session_format.replace('"namespace_pid": -1', '"namespace_pid": %s')
            session_setup = f'''IFS=' ' read -r host_pid rest < /proc/self/stat
printf {shlex.quote(session_format)} "$host_pid" > {shlex.quote(wsl_path(state_path))}
'''
            replay_setup = ''
            if args.replay_packet:
                from pipeline_state import stock_layout
                packet = args.replay_packet.resolve()
                if not packet.is_file() or not packet.with_suffix('.yuv').is_file():
                    raise ValueError('Prepare the replay JSON/YUV pair with replay_ipcm.py first')
                replay_args = ['python3', wsl_path(PROJECT/'tools/replay_ipcm.py'), '--submit', wsl_path(packet),
                               '--layout', json.dumps(stock_layout()), '--frames', '0' if args.replay_continuous else '300', '--fps', '5']
                replay_setup = ' '.join(map(shlex.quote, replay_args))+' >'+shlex.quote(wsl_path(output/'startup-replay.stdout.log'))+' 2>'+shlex.quote(wsl_path(output/'startup-replay.stderr.log'))+' &\n'
            supervision = f'sleep {args.seconds + 5}' if args.seconds else 'while :; do sleep 30; done'
            selected_session = f'export NX300M_SESSION_FILE={shlex.quote(wsl_path(state_path))};\n'
            inner = selected_session + temporary_setup + shim_setup + display_setup + preference_setup + override_setup + proc_setup + card_setup + session_setup + replay_setup + f"chroot --userspec=65534:65534 {shlex.quote(GUEST)} {quoted}; leader=$?; echo CAMERA_LEADER_EXIT=$leader >&2; {supervision}"
            core_setup = f"chmod 1777 {shlex.quote(GUEST)}; trap 'chmod 755 {shlex.quote(GUEST)}' EXIT; ulimit -c unlimited;" if args.core else "ulimit -c 0;"
            timer = f'timeout --signal=TERM --kill-after=2s {args.seconds}s ' if args.seconds else ''
            script = f"{core_setup} {timer}unshare --mount --net --ipc --pid --fork --kill-child sh -c {shlex.quote(inner)}"
        else:
            script = f"ulimit -c 0; timeout --signal=TERM --kill-after=2s 12s unshare --net --ipc chroot --userspec=65534:65534 {shlex.quote(GUEST)} {quoted}"
        started = time.monotonic()
        # Write inside WSL instead of buffering through its Windows stdio relay.
        # Each invocation owns its logs, including while an older run finishes.
        script = f'exec >{shlex.quote(wsl_path(stdout_path))} 2>{shlex.quote(wsl_path(stderr_path))}\n' + script
        if name == 'camera' and not args.seconds:
            # A long-lived Windows wsl.exe supervising an unshare PID tree
            # blocked later WSL launches while the Linux camera remained alive.
            # Keep the Linux supervisor and let its Windows launch client exit.
            launcher = '''import subprocess,sys
p=subprocess.Popen(['sh','-c',sys.argv[1]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
print(p.pid)
'''
            launched = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', '-c', launcher, script], capture_output=True, timeout=20, check=True)
            supervisor = int(launched.stdout.strip())
            summary['runs'][run_name] = {'argv': argv, 'launch_exit_code': launched.returncode,
                'target_exit_code': None, 'detached_linux_supervisor': supervisor,
                'stdout_file': str(stdout_path), 'trace_file': str(stderr_path),
                'raw_screen': args.screen if args.display else None,
                'gpio_substitute': args.gpio_preview, 'device_substitute': args.device_preview,
                'shutter_substitute': args.shutter_preview, 'shutter_park': args.shutter_park, 'isp_substitute': args.isp_preview,
                'capture_boot_input': args.capture_boot, 'ui_mod': args.ui_mod, 'lut_port': args.lut_port,
                'exposure_controls': args.exposure_controls,
                'exposure_read_only': args.exposure_read_only,
                'shutter_limit': args.shutter_limit,
                'color_style': args.color_style, 'lcd_capture': args.lcd_capture,
                'service_loader': args.service_loader,
                'gallery_share': args.gallery_share,
                'isp_color_observer': args.isp_color_observer,'capture_observer':args.capture_observer,
                'capture_geometry': args.capture_geometry,
                'spi_instruction_mmio': args.spi_mmio, 'sensor_register_model': args.sensor_registers}
            summary['runs'][run_name]['cpu_ycc_resize'] = args.cpu_ycc_resize
            summary['runs'][run_name]['cpu_jpeg_decode'] = args.cpu_jpeg_decode
            summary['runs'][run_name]['jpeg_task_trace'] = args.jpeg_task_trace
            summary['runs'][run_name]['jpeg_cpu_diagnostic'] = args.jpeg_cpu_diagnostic
            summary['runs'][run_name]['firmware_mod'] = args.firmware_mod
            summary['runs'][run_name]['firmware_entry_diagnostics'] = args.firmware_entry_diagnostics
            summary['runs'][run_name]['manual_lens_wifi'] = args.manual_lens_wifi
            summary['runs'][run_name]['manual_lens_cs'] = args.manual_lens_cs
            summary['runs'][run_name]['software_isp_source'] = str(args.software_isp_source.resolve()) if args.software_isp_source else None
            summary['runs'][run_name]['diagnostic_sample_source'] = sample_source
            summary['runs'][run_name]['cpu_playback'] = args.cpu_playback
            summary['runs'][run_name]['startup_replay_packet'] = str(args.replay_packet.resolve()) if args.replay_packet else None
            summary['runs'][run_name]['startup_replay_continuous'] = args.replay_continuous
            summary['runs'][run_name]['sdcard'] = args.sdcard
            summary['runs'][run_name]['sdcard_image'] = args.sdcard_image
            summary['runs'][run_name]['shutter_motion'] = args.shutter_motion
            summary['runs'][run_name]['pwm_timing'] = args.pwm_timing
            summary['runs'][run_name]['bayer_replay'] = str(args.bayer_replay.resolve()) if args.bayer_replay else None
            summary['runs'][run_name]['hotshoe_input'] = args.hotshoe_input if args.gpio_preview else None
            summary['runs'][run_name]['shutter_step_us'] = args.shutter_step_us if args.shutter_motion else None
            save_runs(output, summary, args.program)
            print(f'camera: detached Linux supervisor={supervisor}; inspect {state_path} for the actual camera', flush=True)
            return
        result = shell(script, timeout=args.seconds + 15 if args.seconds else None)
        result.stdout, result.stderr = stdout_path.read_bytes(), stderr_path.read_bytes()
        stdout, stderr = result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
        summary["runs"][run_name] = {"argv":argv, "exit_code":result.returncode, "seconds":round(time.monotonic() - started, 3), "stdout_bytes":len(result.stdout), "stderr_bytes":len(result.stderr), "target_exit_groups":re.findall(r"exit_group\((-?\d+)\)", stderr), "target_sigsegv":"--- SIGSEGV" in stderr, "gpio_substitute":bool(name == "camera" and args.gpio_preview), "private_preferences":bool(name == "camera" and args.preferences), "preference_storage_accessible":"PREFMAN_STORAGE_ACCESSIBLE=1" in stderr}
        if args.core:
            summary["runs"][run_name]["core_files"] = [str(path) for path in core_root.glob("qemu_*.core") if path not in old_cores]
        summary["runs"][run_name]["stdout_file"] = str(stdout_path)
        summary["runs"][run_name]["trace_file"] = str(stderr_path)
        summary["runs"][run_name]["device_substitute"] = bool(name == 'camera' and args.device_preview)
        summary['runs'][run_name]['shutter_substitute'] = bool(name == 'camera' and args.shutter_preview)
        summary['runs'][run_name]['shutter_park'] = args.shutter_park
        summary['runs'][run_name]['spi_instruction_mmio'] = bool(name == 'camera' and args.spi_mmio)
        summary['runs'][run_name]['sensor_register_model'] = bool(name == 'camera' and args.sensor_registers)
        summary['runs'][run_name]['mmio_sigsegv_expected'] = bool(name == 'camera' and args.spi_mmio)
        summary['runs'][run_name]['target_terminal_sigsegv'] = bool(re.search(r'uncaught target signal 11|killed by SIGSEGV', stderr))
        summary['runs'][run_name]['isp_substitute'] = bool(name == 'camera' and args.isp_preview)
        summary['runs'][run_name]['cpu_ycc_resize'] = bool(name == 'camera' and args.cpu_ycc_resize)
        summary['runs'][run_name]['cpu_jpeg_decode'] = bool(name == 'camera' and args.cpu_jpeg_decode)
        summary['runs'][run_name]['jpeg_task_trace'] = bool(name == 'camera' and args.jpeg_task_trace)
        summary['runs'][run_name]['jpeg_cpu_diagnostic'] = bool(name == 'camera' and args.jpeg_cpu_diagnostic)
        summary['runs'][run_name]['firmware_mod'] = bool(name == 'camera' and args.firmware_mod)
        summary['runs'][run_name]['firmware_entry_diagnostics'] = bool(name=='camera' and args.firmware_entry_diagnostics)
        summary['runs'][run_name]['manual_lens_wifi'] = bool(name=='camera' and args.manual_lens_wifi)
        summary['runs'][run_name]['manual_lens_cs'] = bool(name=='camera' and args.manual_lens_cs)
        summary['runs'][run_name]['software_isp_source'] = str(args.software_isp_source.resolve()) if name=='camera' and args.software_isp_source else None
        summary['runs'][run_name]['diagnostic_sample_source'] = sample_source if name == 'camera' else None
        summary['runs'][run_name]['cpu_playback'] = bool(name == 'camera' and args.cpu_playback)
        summary['runs'][run_name]['exposure_controls'] = bool(name == 'camera' and args.exposure_controls)
        summary['runs'][run_name]['exposure_read_only'] = bool(name == 'camera' and args.exposure_read_only)
        summary['runs'][run_name]['shutter_limit'] = bool(name == 'camera' and args.shutter_limit)
        summary['runs'][run_name]['color_style'] = bool(name == 'camera' and args.color_style)
        summary['runs'][run_name]['service_loader'] = bool(name == 'camera' and args.service_loader)
        summary['runs'][run_name]['gallery_share'] = bool(name == 'camera' and args.gallery_share)
        summary['runs'][run_name]['isp_color_observer'] = bool(name == 'camera' and args.isp_color_observer)
        summary['runs'][run_name]['capture_observer'] = bool(name == 'camera' and args.capture_observer)
        summary['runs'][run_name]['lcd_capture'] = bool(name == 'camera' and args.lcd_capture)
        summary['runs'][run_name]['capture_geometry'] = bool(name == 'camera' and args.capture_geometry)
        summary["runs"][run_name]["capture_boot_input"] = bool(name == 'camera' and args.capture_boot)
        summary["runs"][run_name]["private_mmap_script_fix"] = bool(name == 'camera' and args.mmap_script_fix)
        if name == 'camera' and args.display: summary['runs'][run_name]['raw_screen'] = args.screen
        summary['runs'][run_name]['syscall_tracing'] = not args.no_strace
        summary['runs'][run_name]['ui_mod'] = args.ui_mod
        summary['runs'][run_name]['lut_port'] = args.lut_port
        summary['runs'][run_name]['simulator_keep_awake'] = args.keep_awake
        summary["runs"][run_name]["target_signals"] = sorted(set(re.findall(r'--- (SIG\w+) ', stderr)))
        summary["runs"][run_name]["assertion_failures"] = re.findall(r'[^\n]*Assertion [^\n]*failed\.', stderr)
        summary["runs"][run_name]["target_sigabrt"] = bool(re.search(r'--- SIG(?:ABRT|IOT) ', stderr))
        print(f"{name}: exit={result.returncode}, stdout={len(result.stdout)} bytes, trace={len(result.stderr)} bytes", flush=True)
        print(stdout[:1800], flush=True)
        print(stderr[-2200:], flush=True)
    save_runs(output, summary, args.program)


if __name__ == "__main__": main()
