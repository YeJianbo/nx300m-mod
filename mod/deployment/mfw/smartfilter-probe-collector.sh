#!/bin/sh
# Private RAM job. Invoked only by smartfilter-probe-launcher --launch.
# READY acknowledges receipt, not apply/snapshot/persistence success.
set -eu
set -f
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH ENV BASH_ENV NX_FN_KEY_LOG
[ "$#" -eq 6 ] || exit 64
nonce=$1; source_root=$2; source_id=$3; state=$4; camera_pid=$5; camera_start=$6
job=$(pwd -P)
case $job in /tmp/nx300m-sf-job.*) ;; *) exit 64 ;; esac
fixture=${NX_SF_PROBE_FIXTURE:-0}
case $fixture:$source_root in 0:/mnt/mmc/nx300m-mod|1:/perf) ;; *) exit 64 ;; esac
case $source_id in ''|*[!0-9:]*|:*|*:|*:*:*) exit 64 ;; esac
case $camera_pid:$camera_start in *[!0-9:]*|:*|*:) exit 64 ;; esac
case $state in /*) ;; *) exit 64 ;; esac
launcher=$job/launcher
loader=$job/loader.arm
blob=$job/blob.bin
guard=/tmp/nx300m-smartfilter-preview.uncertain
preview_lock=/tmp/nx300m-smartfilter-preview.lock
phase=failed
reason=collector_initialization
probe_file=
probe_id=
token=
error_stage=
error_operation=
error_reason=
error_errno=
error_errno_valid=
error_receipt=
error_stderr=
error_facts=
finish() {
    rc=$?
    trap - 0
    printf 'phase=%s\nreason=%s\ntoken=%s\nprobe_file=%s\nram=%s\n' \
        "$phase" "$reason" "$token" "$probe_file" "$job" > "$job/result.tmp"
    if [ -n "$error_stage" ]; then
        printf 'error_stage=%s\nerror_operation=%s\nerror_reason=%s\nerror_errno=%s\nerror_errno_valid=%s\nerror_receipt=%s\nerror_stderr=%s\n' \
            "$error_stage" "$error_operation" "$error_reason" "$error_errno" "$error_errno_valid" \
            "$error_receipt" "$error_stderr" >> "$job/result.tmp"
        printf '%s' "$error_facts" >> "$job/result.tmp"
    fi
    mv "$job/result.tmp" "$job/result"
    printf 'probe phase=%s reason=%s ram=%s\n' "$phase" "$reason" "$job"
    if [ -n "$error_stage" ]; then
        printf 'probe file error stage=%s operation=%s reason=%s errno=%s valid=%s stderr=%s\n' \
            "$error_stage" "$error_operation" "$error_reason" "$error_errno" "$error_errno_valid" "$error_stderr"
    fi
    exit "$rc"
}
trap finish 0
stop() { phase=$1; reason=$2; exit 1; }
# One field, no duplicate-key guessing. Loader receipts are compact JSON but
# whitespace is accepted. Invalid/absent fields fail closed, never imply success.
json_value() {
    awk -v key="$1" -v kind="${3:-any}" '
        { text=$0; pattern="\"" key "\"[ \t]*:[ \t]*"
          while (match(text,pattern)) {
            text=substr(text,RSTART+RLENGTH); count++
            if (substr(text,1,1)=="\"") {
                if (kind!="any" && kind!="string") { bad=1; exit 1 }
                text=substr(text,2); end=index(text,"\""); if (!end) exit 1
                value=substr(text,1,end-1); text=substr(text,end+1)
            } else {
                value=text; sub(/[,} \t\r].*$/,"",value)
                if (kind=="string" || (kind=="number" && value!~/^[0-9]+$/) ||
                    (kind=="boolean" && value!~/^(true|false)$/)) { bad=1; exit 1 }
                text=substr(text,length(value)+1)
            }
          }
        }
        END { if (bad || count!=1) exit 1; print value }
    ' "$2"
}
# Only bounded enum/number/boolean fields enter the line-based RAM result.
# Full escaped text remains in the original JSON and stderr, never evaluated.
capture_error() {
    detail_file=$job/$1.json
    detail_stage=$(json_value error_stage "$detail_file" string 2>/dev/null) || return 0
    detail_operation=$(json_value error_operation "$detail_file" string 2>/dev/null) || return 0
    detail_reason=$(json_value error_reason "$detail_file" string 2>/dev/null) || return 0
    for detail_enum in "$detail_stage" "$detail_operation" "$detail_reason"; do
        case $detail_enum in ''|*[!a-z0-9_]*) return 0 ;; esac
        [ "${#detail_enum}" -le 64 ] || return 0
    done
    detail_errno=$(json_value error_errno "$detail_file" number 2>/dev/null) || return 0
    detail_valid=$(json_value error_errno_valid "$detail_file" boolean 2>/dev/null) || return 0
    case $detail_errno in ''|*[!0-9]*) return 0 ;; esac
    [ "${#detail_errno}" -le 5 ] && [ "$detail_errno" -le 65535 ] || return 0
    case $detail_valid in
        true) [ "$detail_errno" -gt 0 ] || return 0 ;;
        false) [ "$detail_errno" = 0 ] || return 0 ;;
        *) return 0 ;;
    esac
    detail_facts=
    for detail_key in snapshot_created persisted write_attempted destination_may_have_changed destination_bytes_verified; do
        detail_value=$(json_value "$detail_key" "$detail_file" boolean 2>/dev/null) || return 0
        case $detail_value in true|false) ;; *) return 0 ;; esac
        [ "$detail_key:$detail_value" != persisted:true ] || return 0
        detail_facts="${detail_facts}${detail_key}=${detail_value}
"
    done
    error_stage=$detail_stage
    error_operation=$detail_operation
    error_reason=$detail_reason
    error_errno=$detail_errno
    error_errno_valid=$detail_valid
    error_receipt=$detail_file
    error_stderr=$job/$1.stderr
    error_facts=$detail_facts
}
source_present() {
    [ ! -L "$source_root" ] && [ -d "$source_root" ] &&
        [ "$(stat -c '%d:%i' "$source_root" 2>/dev/null)" = "$source_id" ]
}
media_ready() { [ ! -L "$state" ] && [ -d "$state" ] && [ ! -e "$state/media-paused" ] && source_present; }
camera_present() {
    executable=$(readlink "/proc/$camera_pid/exe" 2>/dev/null) || return 1
    if [ "$fixture" = 1 ]; then
        case $executable in /mnt/mmc/bin/smartfilter_fixture.arm|/perf/smartfilter_fixture.arm) ;; *) return 1 ;; esac
    else
        [ "$executable" = /usr/bin/di-camera-app-nx300 ] || return 1
    fi
    IFS= read -r fields < "/proc/$camera_pid/stat" || return 1
    fields=${fields##*) }
    set -- $fields
    [ "$#" -ge 20 ] && [ "$1" != Z ] || return 1
    shift 19
    [ "$1" = "$camera_start" ]
}
run_once() {
    label=$1; shift
    rc=0
    "$launcher" --run "$timeout" "$job/$label.json" "$job/$label.stderr" "$guard" "$@" || rc=$?
    if [ "$rc" -eq 124 ]; then stop uncertain "${label}_deadline_no_process_killed"; fi
    if [ "$rc" -ne 0 ]; then capture_error "$label"; fi
    if [ "$label" = diag ] && [ "$rc" -ne 0 ]; then
        status=$(json_value status "$job/diag.json") || status=
        [ "$status" != no_data ] || stop no_data no_resident_snapshot_created
    fi
    if [ "$label" = save ] && [ "$rc" -ne 0 ]; then
        status=$(json_value status "$job/save.json") || status=
        case $status in
            capture_observed|media_identity_changed|identity_mismatch) stop cancelled "$status" ;;
        esac
    fi
    [ "$rc" -eq 0 ] || stop failed "${label}_failed_${rc}"
}
loader_once() {
    label=$1; shift
    [ "$fixture" != 1 ] || set -- "$@" --fixture
    run_once "$label" "$loader" "$@"
}
timeout=${NX_SF_PROBE_TIMEOUT:-30}
delay=${NX_SF_PROBE_DELAY:-3}
case $timeout in ''|*[!0-9]*) stop refused invalid_timeout ;; esac
case $delay in ''|*[!0-9]*) stop refused invalid_delay ;; esac
[ "${#timeout}" -le 2 ] && [ "$timeout" -ge 1 ] && [ "$timeout" -le 30 ] || stop refused invalid_timeout
[ "${#delay}" -le 2 ] && [ "$delay" -le 10 ] || stop refused invalid_delay
# The helper uses this shell's PID/starttime, then waits for nonce-matching ACK.
# No lock, source access, target creation or loader call precedes this handshake.
"$launcher" --ready "$job" "$nonce" || stop refused ready_not_acknowledged
[ ! -e "$guard" ] && [ ! -L "$guard" ] || stop uncertain previous_command_unresolved
[ ! -L "$preview_lock" ] || stop refused preview_lock_symlink
exec 9>"$preview_lock" || stop refused preview_lock_open
flock -n 9 || stop refused preview_busy
[ ! -e "$guard" ] && [ ! -L "$guard" ] || stop uncertain previous_command_unresolved
[ ! -L "$state" ] && [ -d "$state" ] || stop refused state_unavailable
[ ! -L "$state/media-io.lock" ] || stop refused media_lock_symlink
exec 6>"$state/media-io.lock" || stop refused media_lock_open
flock -s -n 6 || stop refused media_paused_or_draining
media_ready || stop cancelled source_changed_or_media_paused
camera_present || stop cancelled camera_identity_changed
[ ! -e "/var/run/nx300m-ipcm-preview-$camera_pid.state" ] || stop refused ipcm_backend_resident
# Native openat/O_EXCL/O_NOFOLLOW creates an exclusive empty file using exactly
# the loader name contract. FAT permissions are mount-synthesized, not POSIX
# access protection; the fixed production FAT path uses bound media FDs instead
# of synthesized UID ownership. POSIX/fixture owner checks and nofollow remain.
# The pre-save target identity check below is retained, not a claim that its
# check-to-loader-open same-name replacement window is completely closed.
run_once prepare "$launcher" --prepare "$source_root" "$source_id" "$nonce"
IFS=' ' read -r probe_file probe_id extra < "$job/prepare.json" || stop failed target_receipt_absent
[ -z "${extra:-}" ] || stop failed target_receipt_invalid
case $probe_file in
    "$source_root/logs/sf-probe-$nonce.ndjson"|"$source_root/sf-probe-$nonce.ndjson") ;;
    *) stop failed target_receipt_path ;;
esac
case $probe_id in ''|*[!0-9:]*|:*|*:|*:*:*) stop failed target_receipt_identity ;; esac
media_ready && camera_present || stop cancelled identity_changed_before_apply
loader_once apply apply --probe-once --pid "$camera_pid" --blob "$blob"
token=$(json_value token "$job/apply.json") || stop failed apply_token_absent
case $token in ''|*[!0-9:]*|:*|*:|*::*) stop failed apply_token_invalid ;; esac
# Exactly five decimal fields; bind to the original camera PID/starttime too.
old_ifs=$IFS; IFS=:; set -- $token; IFS=$old_ifs
[ "$#" -eq 5 ] && [ -n "$1" ] && [ -n "$2" ] && [ -n "$3" ] && [ -n "$4" ] && [ -n "$5" ] || stop failed apply_token_invalid
[ "$1" = "$camera_pid" ] && [ "$2" = "$camera_start" ] || stop cancelled apply_camera_token_mismatch
printf '%s\n' "$token" > "$job/token"
# A single finite delay and a single freeze/export. Never poll/re-arm/retry.
sleep "$delay"
camera_present || stop cancelled camera_identity_changed_after_apply
loader_once diag diag-export --pid "$camera_pid" --blob "$blob" --expect "$token"
status=$(json_value status "$job/diag.json") || stop failed export_status_absent
snapshot=$(json_value snapshot_created "$job/diag.json") || stop failed export_snapshot_absent
if [ "$status" = no_data ]; then stop no_data no_resident_snapshot_created; fi
[ "$snapshot" = true ] || stop no_data snapshot_not_created
export_token=$(json_value token "$job/diag.json") || stop failed export_token_absent
[ "$export_token" = "$token" ] || stop cancelled export_token_mismatch
# diag-export has no --diag-log: even capture/source changes can only leave RAM
# evidence. probe-save itself checks capture baselines and the frozen window.
media_ready && camera_present || stop cancelled source_changed_or_media_paused_before_save
[ ! -L "$probe_file" ] && [ -f "$probe_file" ] && [ ! -s "$probe_file" ] &&
    [ "$(stat -c '%d:%i' "$probe_file" 2>/dev/null)" = "$probe_id" ] || stop cancelled precreated_target_changed
loader_once save probe-save --pid "$camera_pid" --blob "$blob" --expect "$token" \
    --probe-file "$probe_file" --source-id "$source_id" --ack-idle-no-capture
persisted=$(json_value persisted "$job/save.json") || stop failed save_receipt_absent
observed=$(json_value observed_capture_unchanged "$job/save.json") || stop failed save_capture_receipt_absent
contract=$(json_value probe_contract "$job/save.json") || stop failed save_contract_absent
save_token=$(json_value token "$job/save.json") || stop failed save_token_absent
[ "$save_token" = "$token" ] || stop failed save_receipt_token_mismatch
[ "$persisted" = true ] && [ "$observed" = true ] && [ "$contract" = idle-no-capture ] || stop cancelled save_not_confirmed
phase=persisted
reason=idle_no_capture_probe_saved
# Do not claim safe_to_persist or global camera/media idleness. backend_pending
# is deliberately not rewritten; the original loader receipts remain in RAM.
exit 0
