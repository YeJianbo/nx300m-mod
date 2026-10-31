# This helper belongs to the single-module offline candidate.
nx300m_launch_persistent_app() {
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
        IFS= read -r nx300m_profile < "$nx300m_profile_file" || :
    fi
    case "$nx300m_profile" in
        manual-lens-wifi) ;;
        *) exec "$@" ;;
    esac
    nx300m_library=/usr/lib/nx300m-mod/manual-lens-wifi.so
    if [ ! -f "$nx300m_library" ] || [ ! -r "$nx300m_library" ] || [ ! -s "$nx300m_library" ]; then
        exec "$@"
    fi
    NX300M_MANUAL_LENS_WIFI=1
    LD_PRELOAD=$nx300m_library${LD_PRELOAD:+:$LD_PRELOAD}
    export NX300M_MANUAL_LENS_WIFI LD_PRELOAD
    exec "$@"
}
