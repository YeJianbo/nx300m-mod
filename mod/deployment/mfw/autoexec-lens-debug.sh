#!/bin/sh
# Finite observations and an explicitly requested one-shot no-lens preference.
# Record entry before prerequisites; never load a SO or restart the camera app.
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
root=/mnt/mmc/nx300m-lens-debug
[ -d "$root" ] || exit 0
[ ! -e "$root/DISABLED" ] || exit 0
umask 077
entry=$root/entry.log
printf 'entry_version=5\nstage=entered\n' > "$entry" || exit 0
note() { printf '%s\n' "$*" >> "$entry"; }
date -u >> "$entry" 2>&1
id >> "$entry" 2>&1
run_id=default
if [ -r "$root/RUN_ID" ]; then IFS= read -r run_id < "$root/RUN_ID" || :; fi
case $run_id in ""|*[!0-9A-Za-z-]*) note 'failure=invalid_run_id'; exit 0 ;; esac
[ "${#run_id}" -le 64 ] || { note 'failure=run_id_too_long'; exit 0; }
note "run_id=$run_id"
[ -r "$root/manual-lens-probe.arm" ] || { note 'failure=probe_missing'; exit 0; }
ram_tmp=$(CDPATH= cd -P /tmp && pwd -P) || { note 'failure=tmp_unavailable'; exit 0; }
if ! awk -v path="$ram_tmp" '
    $2=="/" || path==$2 || index(path,$2 "/")==1 {
        if(length($2)>best) {best=length($2); type=$3; options=$4}
    }
    END {exit !(type=="tmpfs" && options !~ /(^|,)noexec(,|$)/ && options ~ /(^|,)rw(,|$)/)}
' /proc/mounts; then
    note 'failure=tmp_not_writable_executable_tmpfs'
    cat /proc/mounts >> "$entry" 2>&1
    exit 0
fi
ram_base=$ram_tmp/nx300m-lens-debug
[ ! -L "$ram_base" ] || { note 'failure=ram_base_symlink'; exit 0; }
mkdir -p "$ram_base" || { note 'failure=ram_base_mkdir'; exit 0; }
source_id=$(stat -c '%d:%i' "$root") || { note 'failure=source_stat'; exit 0; }
for command in nice renice timeout cp cmp chmod stat; do
    command -v "$command" >/dev/null 2>&1 || { note "failure=missing_$command"; exit 0; }
done
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
launch='
    root=$1; ram=$2; source_id=$3; run_id=$4
    entry=$root/entry.log
    printf "stage=worker_entered\n" >> "$entry"
    umask 077
    for path in /proc/self/fd/*; do
        fd=${path##*/}
        case $fd in ""|*[!0-9]*|0|1|2) continue ;; esac
        eval "exec $fd>&-"
    done
    cd / || exit 1
    [ ! -L "$ram" ] || { printf "failure=ram_symlink\n" >> "$entry"; exit 1; }
    if ! mkdir "$ram" 2>/dev/null; then
        printf "stage=run_directory_already_present\n" >> "$entry"
        exit 0
    fi
    finish() {
        result=$?
        trap - 0
        printf "worker_exit=%s\n" "$result" >> "$ram/session.txt"
        if [ "$(stat -c "%d:%i" "$root" 2>/dev/null)" != "$source_id" ]; then
            printf "source_identity_changed\n" >> "$ram/bootstrap.log"
            exit "$result"
        fi
        destination=$root/logs/$run_id
        [ ! -L "$root/logs" ] && [ ! -L "$destination" ] || exit 1
        mkdir -p "$destination" || exit 1
        for path in "$ram"/*.txt "$ram"/*.json "$ram"/*.log; do
            [ -f "$path" ] || continue
            target=$destination/${path##*/}
            [ ! -L "$target" ] || exit 1
            cp "$path" "$target" || exit 1
            cmp -s "$path" "$target" || exit 1
        done
        printf "stage=worker_finished exit=%s\n" "$result" >> "$entry"
        printf "worker_exit=%s; inspect sample exit codes\n" "$result" > "$destination/complete.txt"
        exit "$result"
    }
    trap finish 0
    exec >> "$ram/bootstrap.log" 2>&1
    stage() { printf "stage=%s\n" "$1" >> "$ram/session.txt"; }
    fail() { printf "failure=%s\n" "$1" >> "$ram/session.txt"; exit 1; }
    stage preparing
    renice 19 -p "$$" || printf "warning=renice_failed\n" >> "$ram/session.txt"
    cp "$root/manual-lens-probe.arm" "$ram/probe.arm" || fail probe_copy
    cmp -s "$root/manual-lens-probe.arm" "$ram/probe.arm" || fail probe_readback
    chmod 700 "$ram/probe.arm" || fail probe_permissions
    cp "$root/candidates/manual_lens_release.sh" "$ram/manual_lens_release.sh" || fail release_script_copy
    cmp -s "$root/candidates/manual_lens_release.sh" "$ram/manual_lens_release.sh" || fail release_script_readback
    release_action=status
    if [ -e "$root/ENABLE_NO_LENS_RELEASE" ]; then
        release_run_id=
        IFS= read -r release_run_id < "$root/ENABLE_NO_LENS_RELEASE" || :
        [ "$release_run_id" = "$run_id" ] || fail release_request_run_mismatch
        release_action=enable
    fi
    printf "no_lens_release_action=%s\n" "$release_action" >> "$ram/session.txt"
    boot_id=unavailable
    if [ -r /proc/sys/kernel/random/boot_id ]; then
        IFS= read -r boot_id < /proc/sys/kernel/random/boot_id || boot_id=unavailable
    fi
    {
        printf "boot_id=%s\nsource_id=%s\nrun_id=%s\n" "$boot_id" "$source_id" "$run_id"
        date -u
        id
        uname -srm
        printf "%s\n" "-- cmdline --"; cat /proc/cmdline
        printf "%s\n" "-- mounts --"; cat /proc/mounts
    } >> "$ram/session.txt" 2>&1
    sample=0
    stage sampling
    for delay in 0 10 20; do
        [ "$delay" -eq 0 ] || sleep "$delay"
        if timeout -t 3 "$ram/probe.arm" > "$ram/sample-$sample.json" 2> "$ram/sample-$sample.stderr.txt"; then
            result=0
        else
            result=$?
        fi
        printf "sample=%s exit=%s\n" "$sample" "$result" >> "$ram/session.txt"
        if [ "$sample" -eq 0 ]; then
            if timeout -t 12 /bin/sh "$ram/manual_lens_release.sh" "$release_action" > "$ram/no-lens-release.txt" 2>&1; then
                printf "no_lens_release_status_exit=0\n" >> "$ram/session.txt"
                if [ "$release_action" = enable ] && [ "$(stat -c "%d:%i" "$root" 2>/dev/null)" = "$source_id" ]; then
                    if rm "$root/ENABLE_NO_LENS_RELEASE"; then
                        printf "no_lens_release_request_consumed=1\n" >> "$ram/session.txt"
                    else
                        printf "warning=release_request_cleanup_failed\n" >> "$ram/session.txt"
                    fi
                fi
            else
                printf "no_lens_release_status_exit=%s\n" "$?" >> "$ram/session.txt"
            fi
            camera_pid=$(sed -n "s/.*\"pid\":\([0-9][0-9]*\),.*/\1/p" "$ram/sample-0.json")
            case $camera_pid in
                ""|0|*[!0-9]*) : ;;
                *)
                    # Keep only read-only mapping/status and loader settings.
                    cat "/proc/$camera_pid/maps" > "$ram/camera-maps.txt" 2>&1
                    cat "/proc/$camera_pid/status" > "$ram/camera-status.txt" 2>&1
                    tr "\000" "\n" < "/proc/$camera_pid/environ" |
                        sed -n "/^LD_PRELOAD=/p; /^NX300M_/p" > "$ram/camera-loader.txt"
                    ;;
            esac
        fi
        sample=$((sample+1))
    done
    stage sampled
'
note 'stage=worker_launch_requested'
nice -n 19 /bin/sh -c "$launch" sh "$root" "$ram_base/$run_id" "$source_id" "$run_id" </dev/null >/dev/null 2>&1 &
exit 0
