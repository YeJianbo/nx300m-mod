#!/bin/sh
# Read-only capability check. Never mounts debugfs or changes a tracing control.
set -eu
printf '=== kernel ===\n'
uname -a
cat /proc/version
printf '=== tracefs/debugfs mounts ===\n'
awk '$3 == "debugfs" || $3 == "tracefs" { print }' /proc/mounts
for trace in /sys/kernel/tracing /sys/kernel/debug/tracing; do
    [ -d "$trace" ] || continue
    printf '=== %s ===\n' "$trace"
    for name in available_tracers current_tracer tracing_on tracing_cpumask; do
        if [ -r "$trace/$name" ]; then printf '%s: ' "$name"; cat "$trace/$name"; fi
    done
    if [ -r "$trace/available_filter_functions" ]; then
        awk '$1 ~ /^(ipcs_k_open|ipcs_k_release|d4_kdd_open|d4_kdd_close|ipcs_k_pmu_on_off|ipcs_k_dma_rmu_bus|wait_for_stable|clk_get_sys|clk_enable|clk_set_rate)$/ {print}' "$trace/available_filter_functions"
    fi
    for item in instances kprobe_events events/sched/sched_switch events/sched/sched_wakeup events/irq; do
        [ ! -e "$trace/$item" ] || printf 'available: %s\n' "$item"
    done
done
if [ -r /proc/config.gz ]; then
    printf '=== running kernel config ===\n'
    zcat /proc/config.gz | awk '/CONFIG_(FTRACE|FUNCTION_TRACER|FUNCTION_GRAPH_TRACER|KPROBES|KPROBE_EVENTS|PERF_EVENTS|SCHEDSTATS|IKCONFIG)/ {print}'
fi
