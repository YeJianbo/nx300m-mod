"""Build a RAM-resident service kit for the verified MFW autoexec entry."""
import json
from pathlib import Path
import tarfile
import zipfile

from build_sd_bundle import arm_elf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/mfw-services"


def main():
    files = {
        "autoexec.sh": ROOT / "mod/deployment/mfw/autoexec-services.sh",
        "nx300m-mod/mfw-services.sh": ROOT / "mod/deployment/mfw/services.sh",
        "nx300m-mod/mfw-ram-stage.sh": ROOT / "mod/deployment/mfw/ram-stage.sh",
        "nx300m-mod/bin/nx-remote-controller.arm": ROOT / "outputs/metol/remote/nx-remote-controller.arm",
        "nx300m-mod/bin/nxui.arm": ROOT / "outputs/metol/nxui.arm",
        "nx300m-mod/bin/nx-quickshare-armv7": ROOT / "outputs/quickshare/nx-quickshare-armv7",
        "nx300m-mod/licenses/metol-AGPL-3.0.txt": ROOT / "mod/metol/LICENSE",
        "nx300m-mod/licenses/metol-ATTRIBUTION.md": ROOT / "mod/metol/ATTRIBUTION.md",
        "nx300m-mod/licenses/Nuklear-MIT.txt": ROOT / "work/reference/metol/test_server/nxui/vendor/LICENSE",
        "nx300m-mod/mfw-panel.sh": ROOT / "mod/deployment/mfw/panel.sh",
        "nx300m-mod/mfw-panel-actions.sh": ROOT / "mod/deployment/mfw/panel-actions.sh",
        "nx300m-mod/gui_mfw.NX300M": ROOT / "mod/deployment/mfw/gui_mfw.NX300M",
        "nx300m-mod/gui_preview.NX300M": ROOT / "mod/deployment/mfw/gui_preview.NX300M",
        "nx300m-mod/fonts/fonts.conf.in": ROOT / "mod/deployment/mfw/fonts.conf.in",
        "nx300m-mod/fonts/mfw-font.charset": ROOT / "outputs/metol/mfw-font.charset",
        "README.md": ROOT / "docs/MFW_SERVICES.md",
    }
    for name in ("common.sh", "start.sh", "service.sh", "stop.sh"):
        files[f"nx300m-mod/{name}"] = ROOT / "mod/deployment" / name
    # The standalone panel uses the installed stock font on internal storage.
    # Copying its entire 10.7 MB file to tmpfs needlessly duplicates it at boot.
    font_name = "usr/share/fonts/SDIC_GP_US_20120720.ttf"
    with tarfile.open(ROOT / "work/stock-1.15/rootfs.tar") as stock:
        member = stock.getmember(font_name)
        if not member.isfile() or not member.size:
            raise ValueError("Original NX300M panel font unavailable")
        stock_font_bytes = member.size
    web = ROOT / "outputs/metol/remote/web_root"
    if not (web / "index.html").is_file():
        raise ValueError("Build the remote web client first")
    for path in sorted(web.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Unexpected web asset symlink: {path}")
        if path.is_file():
            files["nx300m-mod/web_root/" + path.relative_to(web).as_posix()] = path
    runtime_list = OUT / "assets/runtime-files.list"
    files["nx300m-mod/runtime-files.list"] = runtime_list
    runtime_list.write_text(
        "".join(name.removeprefix("nx300m-mod/") + "\n"
                for name in sorted(files) if name.startswith("nx300m-mod/")),
        encoding="ascii", newline="\n")
    dependencies = {name: arm_elf(path) for name, path in files.items() if "/bin/" in name}
    inventory = []
    OUT.mkdir(parents=True, exist_ok=True)
    old_staged_font = OUT / "sd/nx300m-mod/fonts/SDIC_GP_US_20120720.ttf"
    if old_staged_font.is_file() and not old_staged_font.is_symlink():
        old_staged_font.unlink()
    archive_path = OUT / "nx300m-mfw-services.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in files.items():
            if source.is_symlink() or source.suffix == ".so":
                raise ValueError(f"Unexpected service-kit input: {source}")
            data = source.read_bytes()
            if name.endswith((".sh", ".md")):
                data = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
            target = OUT / "sd" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(".sh") or "/bin/" in name else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            inventory.append({"path": name, "bytes": len(data)})
    report = {"archive": str(archive_path), "listen": "0.0.0.0:8781",
              "native_dependencies": dependencies, "files": inventory,
              "scope": "RAM-resident independent service and native panel; stock-preview-only X11 Custom shortcut with read-only stock-process validation; explicit preference actions may write source markers; no in-process camera hook or firmware installation",
              "runtime": {"base": "resolved /tmp/nx300m-mfw on writable executable tmpfs",
                          "code": "runtime/", "state": "state/", "bootstrap_log": "bootstrap.log",
                          "verification": "copy every runtime-files.list entry to RAM and compare every byte before execution; no SD fallback",
                          "existing_sd_service": "refuse startup without stopping or migrating the old process",
                          "media_pause": "explicit POST /api/v1/media/pause; poll GET /api/v1/media/status for service_media_released; persistent RAM pause, no automatic resume or stock USB handoff guarantee",
                          "source_identity_env": ["NX300M_MOD_SOURCE_ROOT", "NX300M_MOD_SOURCE_ID"],
                          "control_markers": "DISABLED and NO_PANEL_SHORTCUT mirrored in RAM; TRACE_HTTP and RESTART_SERVICE consumed using RAM receipts without deleting SD markers"},
              "panel_shortcut": "MFW mode defaults NX_REMOTE_CUSTOM_KEY=1: asynchronous single-key Custom grab never freezes the keyboard; stock context is checked only after release and an X11 fence. Valid preview opens the panel; outside preview the saved press/release pair is forwarded to stock. NO_PANEL_SHORTCUT disables grabs and panel launches. Legacy Fn polling remains opt-in with NX_REMOTE_PANEL_SHORTCUT=1",
              "network": {"discovery": "NX_REMOTE 0.9 UDP/5681, on active IPv4 interfaces served by the HTTP listener",
                          "status": "GET /api/v1/network/status; read-only and available while media is paused",
                          "native_panel": "手机连接信息 in native service panel; independent Custom helper needs readable stock 1.15 process and matching live preview; no LUT or exposure module is loaded",
                          "wifi_control": "uses stock Wi-Fi connection; does not start or reconfigure the stock AP",
                          "nfc": "no tag writes or assumed private Samsung tag format"},
              "panel_font_source": font_name,
              "panel_font_storage": "existing internal firmware; not copied to SD or tmpfs",
              "runtime_font_bytes_saved": stock_font_bytes,
              "build_accessed_camera": False}
    (OUT / "build-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Built {len(inventory)} files: {archive_path}")


if __name__ == "__main__":
    main()
