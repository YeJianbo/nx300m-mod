# Run only the original app command with the six native hooks available together.
# The subshell confines the preload environment to the app and its children.
nx300m_launch_app() (
    nx300m_root=/mnt/mmc/nx300m-mod
    nx300m_preload=
    for nx300m_name in \
        lut_port.so ui_mod.so exposure_controls.so shutter_limit.so \
        color-style.so lcd-capture.so
    do
        nx300m_library=$nx300m_root/lib/$nx300m_name
        if [ ! -f "$nx300m_library" ] || [ ! -r "$nx300m_library" ] || [ ! -s "$nx300m_library" ]; then
            exec "$@"
        fi
        nx300m_preload=${nx300m_preload:+$nx300m_preload:}$nx300m_library
    done
    NX300M_MOD_ROOT=$nx300m_root
    LD_PRELOAD=$nx300m_preload${LD_PRELOAD:+:$LD_PRELOAD}
    export NX300M_MOD_ROOT LD_PRELOAD
    exec "$@"
)
