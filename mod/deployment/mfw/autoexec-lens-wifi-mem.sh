#!/bin/sh
# Bounded Wi-Fi admission check. No color module or stock app restart.
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
root=/mnt/mmc/nx300m-lens-debug
[ -d "$root" ] && [ ! -e "$root/DISABLED" ] || exit 0
umask 077
entry=$root/wifi-mem-entry.log
printf 'entry_version=wifi-mem-2\nstage=entered\n' > "$entry" || exit 0
run_id=
IFS= read -r run_id < "$root/WIFI_MEM_RUN_ID" || :
case $run_id in ''|*[!0-9A-Za-z-]*) printf 'failure=invalid_run_id\n' >> "$entry"; exit 0 ;; esac
[ "${#run_id}" -le 64 ] || exit 0
for command in nice timeout cp cmp chmod stat awk mkdir rm; do
    command -v "$command" >/dev/null 2>&1 || exit 0
done
ram_tmp=$(CDPATH= cd -P /tmp && pwd -P) || exit 0
awk -v path="$ram_tmp" '
    $2=="/" || path==$2 || index(path,$2 "/")==1 {
        if(length($2)>best) {best=length($2); type=$3; options=$4}
    }
    END {exit !(type=="tmpfs" && options !~ /(^|,)noexec(,|$)/ && options ~ /(^|,)rw(,|$)/)}
' /proc/mounts || { printf 'failure=tmp_not_executable_tmpfs\n' >> "$entry"; exit 0; }
base=$ram_tmp/nx300m-lens-wifi-mem
[ ! -L "$base" ] || exit 0
mkdir -p "$base" || exit 0
source_id=$(stat -c '%d:%i' "$root") || exit 0
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
worker='
    root=$1; ram=$2; source_id=$3; run_id=$4
    for path in /proc/self/fd/*; do
        fd=${path##*/}
        case $fd in ""|*[!0-9]*|0|1|2) continue ;; esac
        eval "exec $fd>&-"
    done
    cd / || exit 1
    [ ! -L "$ram" ] || exit 1
    mkdir "$ram" 2>/dev/null || exit 0
    finish() {
        result=$?
        trap - 0
        printf "worker_exit=%s\n" "$result" >> "$ram/session.txt"
        [ "$(stat -c "%d:%i" "$root" 2>/dev/null)" = "$source_id" ] || exit "$result"
        destination=$root/logs/$run_id
        [ ! -L "$root/logs" ] && [ ! -L "$destination" ] || exit 1
        mkdir -p "$destination" || exit 1
        for path in "$ram"/*.txt "$ram"/*.json "$ram"/*.log; do
            [ -f "$path" ] || continue
            target=$destination/${path##*/}
            [ ! -L "$target" ] || exit 1
            cp "$path" "$target" && cmp -s "$path" "$target" || exit 1
        done
        printf "worker_exit=%s; inspect apply and after results\n" "$result" > "$destination/complete.txt"
        exit "$result"
    }
    trap finish 0
    exec >> "$ram/worker.log" 2>&1
    printf "run_id=%s\n" "$run_id" > "$ram/session.txt"
    cp "$root/manual-lens-wifi-mem.arm" "$ram/tool.arm" || exit 1
    cmp -s "$root/manual-lens-wifi-mem.arm" "$ram/tool.arm" || exit 1
    chmod 700 "$ram/tool.arm" || exit 1
    timeout -t 8 "$ram/tool.arm" status > "$ram/before.json" 2> "$ram/before.stderr.txt"
    before=$?
    printf "before_exit=%s\n" "$before" >> "$ram/session.txt"
    [ "$before" -eq 0 ] || exit "$before"
    action=status
    action_source=default
    if [ -r "$root/WIFI_MEM_PROFILE" ]; then
        profile=
        IFS= read -r profile < "$root/WIFI_MEM_PROFILE" || :
        case $profile in
            manual-lens-wifi) action=apply; action_source=boot_profile ;;
            stock) : ;;
            *) printf "failure=unknown_boot_profile\n" >> "$ram/session.txt"; exit 1 ;;
        esac
    fi
    if [ -r "$root/WIFI_MEM_REQUEST" ]; then
        request=
        IFS= read -r request < "$root/WIFI_MEM_REQUEST" || :
        case $request in
            "$run_id apply") action=apply ;;
            "$run_id restore") action=restore ;;
            *) printf "failure=request_mismatch\n" >> "$ram/session.txt"; exit 1 ;;
        esac
        action_source=request
        # Requests are one-shot. Only the explicit boot profile may reapply.
        [ "$(stat -c "%d:%i" "$root" 2>/dev/null)" = "$source_id" ] || exit 1
        rm "$root/WIFI_MEM_REQUEST" || exit 1
    fi
    printf "action=%s\naction_source=%s\n" "$action" "$action_source" >> "$ram/session.txt"
    timeout -t 8 "$ram/tool.arm" "$action" > "$ram/action.json" 2> "$ram/action.stderr.txt"
    result=$?
    printf "action_exit=%s\n" "$result" >> "$ram/session.txt"
    timeout -t 8 "$ram/tool.arm" status > "$ram/after.json" 2> "$ram/after.stderr.txt"
    after=$?
    printf "after_exit=%s\n" "$after" >> "$ram/session.txt"
    [ "$result" -eq 0 ] && [ "$after" -eq 0 ]
'
nice -n 19 /bin/sh -c "$worker" sh "$root" "$base/$run_id" "$source_id" "$run_id" </dev/null >/dev/null 2>&1 &
printf 'stage=worker_launched\n' >> "$entry"
exit 0
