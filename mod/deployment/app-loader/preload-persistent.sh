# The stock hibernation path unmounts SD; executable hooks stay in rootfs.
nx300m_launch_persistent_app() {
    # These controls affect a new exec; restored snapshots retain their mappings.
    if [ -e /usr/lib/nx300m-mod/DISABLED ] || [ -L /usr/lib/nx300m-mod/DISABLED ] ||
       [ -e /mnt/mmc/nx300m-mod/DISABLED ] || [ -L /mnt/mmc/nx300m-mod/DISABLED ]; then
        exec "$@"
    fi
    nx300m_profile=stock
    nx300m_profile_file=/usr/lib/nx300m-mod/launch-profile
    if [ -e /mnt/mmc/nx300m-mod/launch-profile ] || [ -L /mnt/mmc/nx300m-mod/launch-profile ]; then
        nx300m_profile_file=/mnt/mmc/nx300m-mod/launch-profile
    fi
    if [ -f "$nx300m_profile_file" ] && [ -r "$nx300m_profile_file" ]; then
        # Read a single token line as data, never source a configuration file.
        IFS= read -r nx300m_profile < "$nx300m_profile_file" || :
    fi
    case "$nx300m_profile" in
        services) nx300m_names='service-loader.so' ;;
        controls) nx300m_names='exposure_controls.so shutter_limit.so lcd-capture.so service-loader.so camera_firmware_mod.so' ;;
        color) nx300m_names='lut_port.so ui_mod.so color-style.so service-loader.so' ;;
        full) nx300m_names='lut_port.so ui_mod.so exposure_controls.so shutter_limit.so color-style.so lcd-capture.so service-loader.so gallery-share.so camera_firmware_mod.so' ;;
        *) exec "$@" ;;
    esac
    nx300m_preload=
    for nx300m_name in $nx300m_names
    do
        nx300m_library=/usr/lib/nx300m-mod/$nx300m_name
        if [ ! -f "$nx300m_library" ] || [ ! -r "$nx300m_library" ] || [ ! -s "$nx300m_library" ]; then
            exec "$@"
        fi
        nx300m_preload=${nx300m_preload:+$nx300m_preload:}$nx300m_library
    done
    NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod
    LD_PRELOAD=$nx300m_preload${LD_PRELOAD:+:$LD_PRELOAD}
    export NX300M_MOD_ROOT LD_PRELOAD
    exec "$@"
}
