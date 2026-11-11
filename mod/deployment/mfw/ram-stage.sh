#!/bin/sh
# SD is a read-only source here. Failed staging never executes the SD service.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
# This script runs before common.sh exists; mirror fatal errors to the card.
fn_note() {
    [ -n "${NX_FN_KEY_LOG:-}" ] || return 0
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$NX_FN_KEY_LOG" 2>/dev/null || :
}
fail() { fn_note "ram stage fail: $*"; printf 'NX300M RAM staging: %s\n' "$*" >&2; exit 1; }
run=0
deferred=0
mode=mfw
if [ "${1:-}" = --service ]; then mode=service; shift; fi
if [ "${1:-}" = --run ]; then run=1; shift; fi
if [ "${1:-}" = --deferred ]; then deferred=1; shift; fi
fn_note "ram stage begin mode=$mode run=$run"
[ "$#" -eq 2 ] || fail 'expected source root and RAM directory'
SOURCE=$(CDPATH= cd -P "$1" && pwd -P) || exit 1
RAM=$(CDPATH= cd -P "$2" && pwd -P) || exit 1
case $SOURCE:$RAM in *[!A-Za-z0-9_./:-]*) fail 'unsupported resource path characters' ;; esac
case $RAM in */nx300m-mfw) ;; *) fail 'unexpected RAM directory' ;; esac
awk -v path="$RAM" '
    $2=="/" || path==$2 || index(path,$2 "/")==1 {
        if (length($2)>best) {best=length($2); type=$3; options=$4}
    }
    END {exit !(type=="tmpfs" && options !~ /(^|,)noexec(,|$)/ && options ~ /(^|,)rw(,|$)/)}
' /proc/mounts || fail 'runtime must be on writable executable tmpfs'
ROOT=$RAM/runtime
STATE=$RAM/state
[ ! -L "$ROOT" ] || fail 'RAM runtime is a symbolic link'
[ ! -L "$STATE" ] || fail 'RAM state is a symbolic link'
SOURCE_ID=$(stat -c '%d:%i' "$SOURCE") || fail 'cannot identify SD source directory'
if [ "$run" -eq 1 ] || [ "$deferred" -eq 1 ]; then
    [ "${NX300M_MOD_SOURCE_ID:-}" = "$SOURCE_ID" ] || fail 'SD source directory identity changed after staging'
fi
export NX300M_MOD_SOURCE_ROOT="$SOURCE" NX300M_MOD_SOURCE_ID="$SOURCE_ID"
export NX300M_MFW_RAM_BASE="$RAM" NX300M_MOD_ROOT="$ROOT" NX_MOD_ROOT="$ROOT" NX_STATE_DIR="$STATE"
if [ "$mode" = service ]; then default_listen=127.0.0.1:8781; else default_listen=0.0.0.0:8781; fi
export NX_REMOTE_LISTEN=${NX_REMOTE_LISTEN:-$default_listen} DISPLAY=${DISPLAY:-:0}
if [ "$mode" = mfw ]; then
    export NX_REMOTE_CUSTOM_KEY=${NX_REMOTE_CUSTOM_KEY:-1}
fi
unset LD_PRELOAD LD_AUDIT
[ ! -L "$STATE/control-receipts" ] || fail 'RAM marker receipts are a symbolic link'
mkdir -p "$STATE/control-receipts"

marker_identity() { stat -c '%d:%i:%s:%Y' "$SOURCE/$1"; }
marker_new() {
    [ -e "$SOURCE/$1" ] || return 1
    [ -f "$SOURCE/$1" ] && [ ! -L "$SOURCE/$1" ] || fail 'source control marker is not a regular file'
    marker_id=$(marker_identity "$1") || fail 'cannot inspect source control marker'
    old_id=
    if [ -r "$STATE/control-receipts/$1.id" ]; then
        IFS= read -r old_id < "$STATE/control-receipts/$1.id" || :
    fi
    [ "$marker_id" != "$old_id" ] || ! cmp -s "$SOURCE/$1" "$STATE/control-receipts/$1.copy"
}
snapshot_marker() {
    snapshot_id=$(marker_identity "$1") || fail 'source control marker disappeared'
    cp "$SOURCE/$1" "$STATE/control-receipts/$1.pending-copy" || fail 'cannot snapshot source marker into RAM'
    cmp -s "$SOURCE/$1" "$STATE/control-receipts/$1.pending-copy" || fail 'source control marker changed while copying'
    [ "$(marker_identity "$1")" = "$snapshot_id" ] || fail 'source control marker identity changed while copying'
    printf '%s\n' "$snapshot_id" > "$STATE/control-receipts/$1.pending-id"
}
consume_marker() {
    mv "$STATE/control-receipts/$1.pending-copy" "$STATE/control-receipts/$1.copy"
    mv "$STATE/control-receipts/$1.pending-id" "$STATE/control-receipts/$1.id"
}

stage=
file_list=

validate_relative() {
    case $1 in ''|/*|*[!A-Za-z0-9_./-]*|*/|*//*|.|..|./*|../*|*/./*|*/../*|*/.|*/..|.state|.state/*|DISABLED|NO_PANEL_SHORTCUT|TRACE_HTTP|RESTART_SERVICE|.runtime-*) return 1 ;; esac
}
copy_files() {
    set --
    copy_dir=
    while IFS= read -r relative; do
        validate_relative "$relative" || fail 'invalid runtime file list entry'
        [ -f "$SOURCE/$relative" ] && [ ! -L "$SOURCE/$relative" ] || fail "source file missing or linked: $relative"
        if [ "$relative" = runtime-files.list ]; then
            cmp -s "$SOURCE/$relative" "$stage/$relative" || fail 'source file list changed during staging'
            continue
        fi
        case $relative in
            */*) directory=${relative%/*} ;;
            *) directory=. ;;
        esac
        if [ "$directory" != "$copy_dir" ]; then
            if [ "$#" -gt 0 ]; then
                [ "$deferred" -eq 0 ] || deferred_alive || fail 'deferred runtime or source is no longer active'
                cp "$@" "$stage/$copy_dir/" || fail "cannot stage directory: $copy_dir"
            fi
            copy_dir=$directory
            [ -d "$stage/$copy_dir" ] || mkdir -p "$stage/$copy_dir"
            set --
        fi
        set -- "$@" "$SOURCE/$relative"
    done < "$file_list"
    if [ "$#" -gt 0 ]; then
        [ "$deferred" -eq 0 ] || deferred_alive || fail 'deferred runtime or source is no longer active'
        cp "$@" "$stage/$copy_dir/" || fail "cannot stage directory: $copy_dir"
    fi
    # Preserve the manifest snapshot; each other file is copied only into its
    # own directory. One cp per directory avoids per-file cold-boot exec costs.
    set --
    while IFS= read -r relative; do
        set -- "$@" "$stage/$relative"
    done < "$file_list"
    # The complete verify_files pass below compares every copied byte before
    # publication. Per-copy cmp repeated the same SD reads and process launches.
    chmod 400 "$@" || fail 'cannot protect staged assets'
    set --
    while IFS= read -r relative; do
        case $relative in *.sh|bin/*|fn-panel/bin/*) set -- "$@" "$stage/$relative" ;; esac
    done < "$file_list"
    [ "$#" -eq 0 ] || chmod 500 "$@" || fail 'cannot mark staged executables'
}
verify_files() {
    verifier=$1/bin/ram_asset_verify.arm
    [ "$deferred" -eq 0 ] || verifier=$ROOT/bin/ram_asset_verify.arm
    # Bootstrap trust with the stock cmp before executing this RAM-only tool.
    # It then checks every listed source/destination byte in a single process.
    [ -f "$SOURCE/bin/ram_asset_verify.arm" ] && [ ! -L "$SOURCE/bin/ram_asset_verify.arm" ] &&
        [ -f "$verifier" ] && [ ! -L "$verifier" ] &&
        cmp -s "$SOURCE/bin/ram_asset_verify.arm" "$verifier" || return 1
    "$verifier" "$SOURCE" "$1" "$file_list"
}

check_capacity() {
    # Account only the manifest assets, not unrelated SD backups.
    required_kb=0
    set --
    while IFS= read -r relative; do
        validate_relative "$relative" || fail 'invalid runtime file list entry'
        [ -f "$SOURCE/$relative" ] && [ ! -L "$SOURCE/$relative" ] || fail "source file missing or linked: $relative"
        set -- "$@" "$SOURCE/$relative"
    done < "$file_list"
    # BusyBox stat accepts multiple operands. Collect sizes in one process
    # instead of loading the applet once for every small resource on cold boot.
    asset_count=$#
    sizes=$(stat -c '%s' "$@") || fail 'cannot size source assets'
    sized_count=0
    for bytes in $sizes; do
        case $bytes in ''|*[!0-9]*) fail 'invalid source asset size' ;; esac
        required_kb=$((required_kb+((bytes+4095)/4096)*4))
        sized_count=$((sized_count+1))
    done
    [ "$sized_count" -eq "$asset_count" ] || fail 'incomplete source asset sizes'
    # df is a tmpfs quota, not physical RAM availability. Linux 3.5 lacks
    # MemAvailable: subtract Shmem from Cached before estimating reclaimable
    # file cache, so previous tmpfs generations are never counted as free RAM.
    memory=$(awk '
        $1 ~ /^(MemTotal|MemFree|MemAvailable|Buffers|Cached|Shmem):$/ {
            if ($2 !~ /^[0-9]+$/ || $3!="kB") bad=1
            value[$1]=$2; seen[$1]=1
        }
        END {
            if (bad || !seen["MemTotal:"] || !seen["MemFree:"] ||
                !seen["Buffers:"] || !seen["Cached:"] || !seen["Shmem:"]) exit 1
            cache=value["Cached:"]-value["Shmem:"]; if (cache<0) cache=0
            estimate=value["MemFree:"]+value["Buffers:"]+cache
            source="free+buffers+cached-shmem"
            if (seen["MemAvailable:"]) {estimate=value["MemAvailable:"]; source="MemAvailable"}
            printf "%.0f %.0f %.0f %.0f %.0f %.0f %s\n", value["MemTotal:"],
                value["MemFree:"], value["Buffers:"], value["Cached:"], value["Shmem:"], estimate, source
        }
    ' /proc/meminfo) || fail 'cannot estimate physical RAM availability'
    set -- $memory
    [ "$#" -eq 7 ] || fail 'invalid physical RAM estimate'
    physical_available_kb=$6
    # A fixed 32 MiB headroom is deliberately conservative. This preflight is
    # an instantaneous estimate, not a guarantee of camera working-memory needs.
    reserve_kb=32768
    printf 'RAM memory total=%sKiB free=%sKiB buffers=%sKiB cached=%sKiB shmem=%sKiB available=%sKiB estimate=%s required=%sKiB reserve=%sKiB\n' \
        "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$required_kb" "$reserve_kb"
    [ "$physical_available_kb" -ge "$((required_kb+reserve_kb))" ] || \
        fail "insufficient physical RAM headroom: need=${required_kb}KiB reserve=${reserve_kb}KiB available=${physical_available_kb}KiB"
    # Stock BusyBox may choose 512-byte units when this variable is inherited.
    available_kb=$(unset POSIXLY_CORRECT; df -Pk "$RAM" | awk 'END {print $4}')
    case $available_kb in ''|*[!0-9]*) fail 'cannot read RAM filesystem capacity' ;; esac
    [ "$available_kb" -ge "$required_kb" ] || fail "insufficient RAM filesystem space: need=${required_kb}KiB available=${available_kb}KiB"
}

cleanup_stage() {
    result=${1:-$?}
    trap - 0 HUP INT TERM
    case ${stage:-} in
        "$RAM"/stage.*)
            [ ! -L "$stage" ] && rm -rf "$stage" ;;
    esac
    exit "$result"
}
deferred_alive() {
    [ ! -e "$STATE/media-paused" ] && [ ! -e "$SOURCE/DISABLED" ] || return 1
    if [ -f "$STATE/manual-stop" ]; then
        stopped_boot=
        IFS= read -r stopped_boot < "$STATE/manual-stop" || :
        [ "$stopped_boot" != "$worker_boot" ] || return 1
    fi
    [ "$(stat -c '%d:%i' "$ROOT")" = "${NX300M_RUNTIME_ID:-}" ] &&
        [ "$(stat -c '%d:%i' "$SOURCE")" = "$SOURCE_ID" ]
}

if [ "$deferred" -eq 1 ]; then
    [ "$(readlink -f "$0")" = "$ROOT/mfw-ram-stage.sh" ] || fail 'deferred launcher is not the verified RAM copy'
    [ /proc/self/fd/6 -ef "$RAM/stage.lock" ] && flock -n 6 || fail 'deferred staging lease is missing'
    IFS= read -r worker_boot < /proc/sys/kernel/random/boot_id || exit 1
    deferred_alive || fail 'deferred runtime or source is no longer active'
    [ -s "$ROOT/.runtime-deferred.list" ] && [ ! -L "$ROOT/.runtime-deferred.list" ] || fail 'deferred list is unavailable'
    [ ! -e "$ROOT/.runtime-assets-ready" ] || exit 0
    # Media pause cannot acknowledge card release while this worker reads it.
    [ ! -L "$STATE/media-io.lock" ] || fail 'media lease is a symbolic link'
    exec 7>"$STATE/media-io.lock"
    flock -s -n 7 || fail 'media access is paused'
    deferred_alive || fail 'deferred loading was stopped before media admission'
    stage=$(mktemp -d "$RAM/stage.XXXXXX") || fail 'cannot allocate deferred staging directory'
    finish_deferred() {
        worker_result=$?
        if [ "$worker_result" -ne 0 ]; then
            printf 'failed\n' > "$STATE/deferred-assets.state"
            fn_note "deferred assets failed rc=$worker_result; core service retained"
        fi
        cleanup_stage "$worker_result"
    }
    trap finish_deferred 0
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    printf 'loading\n' > "$STATE/deferred-assets.state"
    cp "$ROOT/runtime-files.list" "$stage/runtime-files.list" || fail 'cannot snapshot deferred manifest'
    file_list=$ROOT/.runtime-deferred.list
    check_capacity
    copy_files
    verify_files "$stage" || fail 'deferred source changed or RAM verification failed'
    deferred_alive || fail 'deferred runtime or source changed before publication'
    # Publish the complete website. The core root
    # and daemon inode stay intact, including a panel already open in it.
    if [ -d "$stage/web_root" ]; then
        [ ! -L "$ROOT/web_root" ] || fail 'published web directory is linked'
        mv "$ROOT/web_root" "$stage/web_root.previous" || fail 'cannot retain prior web resources'
        if ! mv "$stage/web_root" "$ROOT/web_root"; then
            mv "$stage/web_root.previous" "$ROOT/web_root" || :
            fail 'cannot publish web resources'
        fi
    fi
    printf 'ready\n' > "$ROOT/.runtime-assets-ready"
    printf 'ready\n' > "$STATE/deferred-assets.state"
    fn_note 'deferred network assets ready; core daemon unchanged'
    exit 0
fi

if [ "$run" -eq 1 ]; then
    [ "$(readlink -f "$0")" = "$ROOT/mfw-ram-stage.sh" ] || fail 'resident launcher is not the verified RAM copy'
    cd "$ROOT"
    if [ "$mode" = service ]; then
        exec /bin/sh "$ROOT/start.sh" </dev/null >> "$STATE/service-entry.log" 2>&1 6>&-
    fi
    # Only this RAM shell waits for the finite diagnostics. The supervisor,
    # remote, panels and any locks/logs inherit RAM paths and cwd.
    set +e
    fn_note 'ram run launching services'
    /bin/sh "$ROOT/mfw-services.sh" </dev/null >> "$STATE/mfw-entry.log" 2>&1 6>&-
    result=$?
    fn_note "ram run services exit=$result"
    set -e
    if [ "$result" -eq 0 ] && [ -e "$STATE/control-receipts/TRACE_HTTP.pending-id" ] && [ ! -e "$ROOT/TRACE_HTTP" ]; then
        consume_marker TRACE_HTTP
    fi
    printf 'RAM service entry exit=%s state=%s\n' "$result" "$STATE"
    if [ "$result" -eq 0 ] && [ -s "$ROOT/.runtime-deferred.list" ] &&
       [ ! -e "$ROOT/.runtime-assets-ready" ]; then
        NX300M_RUNTIME_ID=$(stat -c '%d:%i' "$ROOT")
        export NX300M_RUNTIME_ID
        # Inherit the staging lease, not the services' entry lock. A second
        # generation cannot replace the files while this finite worker runs.
        /bin/sh "$ROOT/mfw-ram-stage.sh" --deferred "$SOURCE" "$RAM" \
            </dev/null >> "$STATE/deferred-assets.log" 2>&1 &
    fi
    exit "$result"
fi

exec 6> "$RAM/stage.lock"
flock -n 6 || { fn_note 'ram stage exit: staging lock busy'; exit 0; }
[ ! -e "$SOURCE/DISABLED" ] || { fn_note 'ram stage exit: DISABLED marker'; exit 0; }
[ -s "$SOURCE/runtime-files.list" ] && [ ! -L "$SOURCE/runtime-files.list" ] || fail 'runtime file list is unavailable'
stage=$(mktemp -d "$RAM/stage.XXXXXX") || fail 'cannot allocate RAM staging directory'
# A failed copy, process-ownership refusal or full tmpfs must not leave a new
# full resource tree behind on every subsequent MFW entry. Only this shell's
# mktemp directory is disposable; published/previous generations are excluded.
trap cleanup_stage 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'RAM staging source=%s source_id=%s candidate=%s\n' "$SOURCE" "$SOURCE_ID" "$stage"
cp "$SOURCE/runtime-files.list" "$stage/runtime-files.list" || fail 'cannot snapshot runtime file list'
cmp -s "$SOURCE/runtime-files.list" "$stage/runtime-files.list" || fail 'source file list changed while copying'
file_list=$stage/runtime-files.list
defer_assets=0
if [ "$mode" = mfw ]; then
    : > "$stage/.runtime-core.list"
    : > "$stage/.runtime-deferred.list"
    while IFS= read -r relative; do
        validate_relative "$relative" || fail 'invalid runtime file list entry'
        case $relative in
            web_root/*) list=.runtime-deferred.list ;;
            *) list=.runtime-core.list ;;
        esac
        printf '%s\n' "$relative" >> "$stage/$list"
    done < "$stage/runtime-files.list"
    if [ -s "$stage/.runtime-deferred.list" ]; then
        defer_assets=1
        file_list=$stage/.runtime-core.list
        chmod 400 "$stage/.runtime-core.list" "$stage/.runtime-deferred.list"
    fi
fi
# Repeated MFW entry must not allocate another full tmpfs tree just to discover
# that the resident bytes already match. The small manifest snapshot also keeps
# both comparisons on the same list if the source is changed while staging.
same=0
if [ -d "$ROOT" ]; then
    core_list=$file_list
    if [ "$defer_assets" -eq 0 ] || [ -f "$ROOT/.runtime-assets-ready" ]; then
        file_list=$stage/runtime-files.list
    fi
    if verify_files "$ROOT"; then
        if [ "$defer_assets" -eq 0 ] ||
           { [ ! -L "$ROOT/.runtime-core.list" ] && [ ! -L "$ROOT/.runtime-deferred.list" ] &&
             cmp -s "$stage/.runtime-core.list" "$ROOT/.runtime-core.list" &&
             cmp -s "$stage/.runtime-deferred.list" "$ROOT/.runtime-deferred.list"; }; then same=1; fi
    fi
    file_list=$core_list
fi
verified=$ROOT
if [ "$same" -eq 0 ]; then
    check_capacity
    copy_files
    verify_files "$stage" || fail 'source changed during staging or RAM verification failed'
    [ "$defer_assets" -eq 0 ] || mkdir -p "$stage/web_root"
    verified=$stage
else
    printf 'RAM runtime bytes unchanged; full staging copy skipped\n'
fi
required_entry='mfw-services.sh mfw-panel.sh mfw-panel-actions.sh gui_mfw.NX300M'
if [ "$mode" = service ]; then required_entry='ram-start.sh panel.sh fonts/SDIC_GP_US_20120720.ttf'; fi
for required in common.sh start.sh stop.sh service.sh mfw-ram-stage.sh bin/ram_asset_verify.arm bin/nx-remote-controller.arm bin/nxui.arm fonts/fonts.conf.in $required_entry; do
    [ -s "$verified/$required" ] || fail "required RAM asset missing: $required"
done

if [ "$defer_assets" -eq 0 ]; then
    [ -s "$verified/web_root/index.html" ] || fail 'network assets are missing'
else
    [ -d "$verified/web_root" ] || fail 'RAM web resource directory is missing'
fi

# Reuse the shared PID/boot/starttime/executable-inode checks without creating
# or updating the old SD state directory. Only the verified RAM common is read.
NX300M_MOD_ROOT=$verified
export NX300M_MOD_ROOT
. "$verified/common.sh"
ROOT=$RAM/runtime
REMOTE=$ROOT/bin/nx-remote-controller.arm
QUICKSHARE=$ROOT/bin/nx-quickshare-armv7
export NX300M_MOD_ROOT="$ROOT"
owned=0
if record_alive; then owned=1; fi
owned_remote_process() {
    [ "$owned" -eq 1 ] && [ "$2" = "$REMOTE" ] || return 1
    record_alive || return 1
    [ "$1" = "$REC_PID" ] && return 0
    # The X11 helpers are direct forks of this exact remote. A matching basename
    # alone cannot establish ownership of another SD or stale service instance.
    helper_start=$(process_start "$1") || return 1
    IFS= read -r helper_comm < "/proc/$1/comm" || return 1
    case $helper_comm in nx-x11-worker|nx-custom-key|nx-menu-key|nx-usb-worker) ;; *) return 1 ;; esac
    IFS= read -r helper_stat < "/proc/$1/stat" || return 1
    helper_fields=${helper_stat##*) }
    set -- "$1" $helper_fields
    [ "$#" -ge 3 ] && [ "$3" = "$REC_PID" ] || return 1
    helper_pid=$1
    identity_alive "$helper_pid" "$helper_start" "$REC_BOOT" "$REC_EXE" "$REC_EXE_ID" || return 1
    record_alive || return 1
    IFS= read -r helper_stat < "/proc/$helper_pid/stat" || return 1
    helper_fields=${helper_stat##*) }
    set -- $helper_fields
    [ "$#" -ge 2 ] && [ "$2" = "$REC_PID" ]
}
processes=$("$verified/bin/ram_asset_verify.arm" --process-scan "$SOURCE") || fail 'cannot audit existing service processes'
while read -r kind pid; do
    case $kind in
        remote)
            executable=$(readlink "/proc/$pid/exe" 2>/dev/null) || continue
            if ! owned_remote_process "$pid" "$executable"; then
                fail "existing remote pid=$pid is not this verified RAM instance; old SD or unknown service was not stopped"
            fi ;;
        shell) fail "old SD service shell pid=$pid remains active; no process was stopped" ;;
    esac
done <<EOF
$processes
EOF

restart=0
if marker_new RESTART_SERVICE; then snapshot_marker RESTART_SERVICE; restart=1; fi
fn_note "ram stage publish decision same=$same restart=$restart owned=$owned"
if [ "$owned" -eq 1 ] && [ "$same" -eq 0 ] && [ "$restart" -eq 0 ]; then
    fail 'RAM assets differ while an owned service is running; a new RESTART_SERVICE request is required'
fi
if [ "$same" -eq 0 ] && [ -e "$ROOT" ] && [ "$restart" -eq 0 ]; then
    # Keep at most one previous generation. It may still be referenced by a
    # panel, so do not delete it merely because the remote process stopped.
    # Refuse before stopping the current service or renaming its runtime.
    # A RESTART_SERVICE boot skips this guard: the request explicitly replaces
    # the service, so a generation left behind by the camera's snapshot
    # restore is discarded below instead of blocking every later deployment.
    for retained in "$RAM"/stage.*.previous; do
        [ ! -e "$retained" ] && [ ! -L "$retained" ] ||
            fail 'a previous RAM generation remains; another replacement would accumulate resources'
    done
fi
if [ "$restart" -eq 1 ] && [ -d "$ROOT" ]; then
    # The previous generation remains intact until its exact-process stop
    # succeeds. Its state and cleanup are RAM-only, unlike the old SD service.
    [ -r "$ROOT/stop.sh" ] && [ -r "$ROOT/common.sh" ] || fail 'previous RAM stop path is unavailable'
    /bin/sh "$ROOT/stop.sh" 6>&- || fail 'owned RAM service stop failed; RAM generation was not replaced'
    fn_note 'ram stage: restart stop complete'
fi
if [ "$restart" -eq 1 ]; then
    # The stop above terminated the daemon and, through its death signal, any
    # panel it had launched. Nothing can still reference a retained generation.
    for retained in "$RAM"/stage.*.previous; do
        if [ -e "$retained" ] || [ -L "$retained" ]; then
            rm -rf "$retained" || fail 'cannot discard retained previous RAM generation'
            fn_note "ram stage: discarded retained generation ${retained##*/}"
        fi
    done
fi
if [ "$same" -eq 0 ]; then
    [ ! -L "$ROOT" ] || fail 'RAM runtime is a symbolic link'
    if [ -e "$ROOT" ]; then
        previous=$stage.previous
        mv "$ROOT" "$previous" || fail 'cannot retain previous RAM generation'
    fi
    mv "$stage" "$ROOT" || fail 'cannot publish verified RAM generation'
    stage=
else
    # Only this invocation's mktemp directory, under verified tmpfs, is removed.
    case $stage in "$RAM"/stage.*) rm -rf "$stage" ;; *) fail 'unexpected staging directory' ;; esac
    stage=
fi
export NX300M_AUTO_START=1
if [ "$restart" -eq 1 ]; then
    consume_marker RESTART_SERVICE
    export NX300M_AUTO_START=0
fi
# Mirror persistent control policy into RAM; original SD markers are untouched.
for marker in DISABLED NO_PANEL_SHORTCUT; do
    if [ -e "$SOURCE/$marker" ]; then : > "$ROOT/$marker"; else rm -f "$ROOT/$marker"; fi
done
rm -f "$ROOT/RESTART_SERVICE"
if marker_new TRACE_HTTP; then
    snapshot_marker TRACE_HTTP
    : > "$ROOT/TRACE_HTTP"
else
    rm -f "$ROOT/TRACE_HTTP"
fi
printf 'RAM runtime ready root=%s state=%s source_id=%s\n' "$ROOT" "$STATE" "$SOURCE_ID"
fn_note "ram runtime ready root=$ROOT"
# Replace the SD-reading shell, closing its script/input descriptors before
# opening the verified RAM launcher. Keep only the RAM staging lock (FD 6).
exec /bin/sh -c '
    for path in /proc/self/fd/*; do
        fd=${path##*/}
        case $fd in ""|*[!0-9]*|0|1|2|6) continue ;; esac
        eval "exec $fd>&-"
    done
    cd "$2/runtime" || exit 1
    if [ "$3" = service ]; then
        exec /bin/sh "$2/runtime/mfw-ram-stage.sh" --service --run "$1" "$2"
    fi
    exec /bin/sh "$2/runtime/mfw-ram-stage.sh" --run "$1" "$2"
' sh "$SOURCE" "$RAM" "$mode"
