#!/bin/sh
# Run only after obtaining a verified camera shell. Output goes to stdout.
# No filesystem redirection, device I/O, service control or firmware execution.

section() { printf '\n== %s ==\n' "$1"; }
read_text() {
    if [ -r "$1" ]; then
        printf '\n-- %s --\n' "$1"
        cat "$1"
    else
        printf 'unavailable: %s\n' "$1"
    fi
}

section 'Kernel and session'
uname -srm
read_text /proc/version
read_text /proc/uptime
read_text /proc/sys/kernel/random/boot_id

section 'Mounts'
read_text /proc/mounts

section 'Original paths and candidate presence'
for path in /usr/bin/di-camera-app /usr/bin/di-camera-app-nx300 \
    /usr/bin/go.sh /usr/bin/hib2.sh /usr/bin/nx300m-camera-launch \
    /opt/share/applications/com.samsung.di-camera-app-nx300.desktop \
    /usr/lib/nx300m-mod /mnt/mmc /sdcard /mnt/mmc/nx300m-mod \
    /mnt/mmc/nx300m-mod/start.sh /mnt/mmc/nx300m-mod/stop.sh; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        ls -ld "$path"
    else
        printf 'absent: %s\n' "$path"
    fi
done

section 'Running processes'
ps

section 'Requested launch policy and service loader'
read_text /usr/lib/nx300m-mod/launch-profile
read_text /mnt/mmc/nx300m-mod/launch-profile
read_text /var/run/nx300m-service-loader-status.json
for path in /usr/lib/nx300m-mod/DISABLED /mnt/mmc/nx300m-mod/DISABLED; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        printf 'disabled marker present: %s\n' "$path"
    else
        printf 'disabled marker absent: %s\n' "$path"
    fi
done
printf 'Launch configuration applies at exec; existing mappings are inspected below.\n'

section 'Camera process and mapped modules'
found=0
for proc in /proc/[0-9]*; do
    [ -r "$proc/comm" ] || continue
    name=$(cat "$proc/comm")
    case "$name" in
        di-camera-app*)
            found=1
            printf '\npid=%s comm=%s\n' "${proc##*/}" "$name"
            readlink "$proc/exe"
            if [ -r "$proc/maps" ]; then
                grep -E 'nx300m-mod|lut_port|ui_mod|exposure_controls|shutter_limit|color-style|lcd-capture|service-loader|gallery-share|camera_firmware_mod' "$proc/maps"
                result=$?
                case "$result" in
                    0) ;;
                    1) printf 'No matching module path in readable maps.\n' ;;
                    *) printf 'Module map inspection failed.\n' ;;
                esac
            else
                printf 'Process maps unavailable.\n'
            fi
            ;;
    esac
done
[ "$found" -eq 1 ] || printf 'No matching camera process found; naming or process visibility may differ.\n'

section 'Done'
printf 'Inspection only. No service started, process signalled, hook loaded or installation performed.\n'
