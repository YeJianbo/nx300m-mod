#!/usr/bin/env python3
"""Strict, stdlib-only reader for schema-1/2 smartfilter RAM timing NDJSON.

API: parse_lines(iterable, expected_build=None), parse_log(Path, ...),
analyze(ParsedLog), to_markdown(report), main(argv=None).

Hash input is the original JSON payload bytes (not an optional logging prefix),
including trailing whitespace and LF. Identical payload retransmissions count
once. CR, if present before LF, is retained in the hash. No eval/shell execution.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Iterable

STAGES = tuple("preview effect ensure initialize select begin try0 try1 try2 try3 "
               "geometry open ade_init lut0 lut1 lut2 flush_in ycc flush_out reset "
               "iq_init iq_process close unlock copy drain prepare finish calibration".split())
TYPES = {"sf_diag_begin", "sf_diag_record", "sf_diag_control", "sf_diag_end"}
PATHS = {"render", "passthrough", "fallback", "unknown"}
REASONS = {0: "unknown", 1: "render", 2: "strength_zero", 3: "disabled", 4: "capture",
           5: "control", 6: "busy", 7: "fault", 8: "fallback", 9: "stock", 10: "no_context",
           11: "probe_exhausted", 12: "probe_blocked"}
PROBE_FLAG = 256
UINT32_MAX = (1 << 32) - 1
PROBE_SLOTS = 128
PROBE_STATES = {0: "OFF", 1: "ARMED", 2: "CLAIMED", 3: "RETURNED", 4: "BLOCKED"}
PROBE_OUTCOMES = dict(enumerate("NONE SUCCESS BUSY CAPTURE RENDER RESTORE UNLOCK TRACE CONTROL FAULT INVALID".split()))
REASON_PATH = {number: ("render" if number == 1 else "fallback" if number == 8 else
                        "unknown" if number in (0, 9) else "passthrough") for number in REASONS}
NS = 1_000_000_000
MAX_LINE_BYTES = 4095
# Deliberately NOT 'search for the first {'. Only short bracket tags / fn: tags.
PREFIX = re.compile(rb"^(?:(?:\[[^\[\]{}\r\n]{1,80}\][ \t]*){1,4}"
                    rb"(?:[A-Za-z_][A-Za-z0-9_.:/-]{0,40}:[ \t]+)?"
                    rb"|[A-Za-z_][A-Za-z0-9_.:/-]{0,40}:[ \t]+)(?=\{)")
ID_HINT = re.compile(r'"dump_id"\s*:\s*("(?:[^"\\]|\\.)*")')


@dataclass
class Dump:
    dump_id: str
    begin: dict | None = None
    end: dict | None = None
    payload: dict[int, tuple[dict, bytes]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass
class ParsedLog:
    dumps: dict[str, Dump] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.dumps) and all(d.valid for d in self.dumps.values())


def _integer(value, name, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{name}: expected integer" + (f" >= {minimum}" if minimum is not None else ""))


def _string(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}: expected nonempty string")


def _pair(value, name):
    if value is not None and (not isinstance(value, list) or len(value) != 2
                              or any(type(v) is not int for v in value)):
        raise ValueError(f"{name}: expected null or [sec, nsec] integers")


def _required_pair(obj, name):
    if name not in obj:
        raise ValueError(f"missing {name}")
    _pair(obj[name], name)


def _build(value):
    return type(value) is int and 0 < value <= 0xFFFFFFFF


def _time(value):
    if (isinstance(value, list) and len(value) == 2
            and all(type(v) is int for v in value)
            and value[0] >= 0 and 0 <= value[1] < NS):
        return value[0] * NS + value[1]
    return None


def _span(record):
    begin, end = _time(record["begin"]), _time(record["end"])
    return None if begin is None or end is None or end < begin else (begin, end)


def _duration(stage):
    return _time([stage["sec"], stage["nsec"]])


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-JSON numeric constant: {value}")


def _validate(obj, expected_build):
    kind = obj["type"]
    _string(obj.get("dump_id"), "dump_id")
    if kind in {"sf_diag_begin", "sf_diag_end"}:
        if type(obj.get("schema")) is not int or obj["schema"] not in (1, 2):
            raise ValueError("schema must be 1 or 2")
    if kind == "sf_diag_begin":
        _integer(obj.get("pid"), "pid", 1)
        # Process-start identifiers may be /proc start ticks, an opaque string,
        # or an absolute sec/nsec pair; preserve the exact identifier in groups.
        start = obj.get("start")
        if not ((type(start) is int and start >= 0) or (isinstance(start, str) and start)
                or _time(start) is not None):
            raise ValueError("start: expected process-start identifier")
        if not _build(obj.get("build")):
            raise ValueError("build: expected nonzero uint32 integer")
        if expected_build is not None and obj["build"] != expected_build:
            raise ValueError(f"build mismatch: expected {expected_build!r}, got {obj['build']!r}")
        window = obj.get("window")
        if not ((type(window) is int and window >= 0) or (isinstance(window, str) and window)):
            raise ValueError("window: expected nonnegative integer or nonempty string")
        look_id = obj.get("look_id")
        if not ((isinstance(look_id, str) and look_id) or (type(look_id) is int and look_id >= 0)):
            raise ValueError("look_id: expected nonempty string or nonnegative integer")
        for key in ("generation", "dropped", "clock_errors"):
            _integer(obj.get(key), key, 0)
        _integer(obj.get("strength"), "strength")
        if obj.get("clock") != "CLOCK_MONOTONIC" or obj.get("unit") != "sec_nsec":
            raise ValueError("clock/unit mismatch")
        if type(obj.get("control_intervals_complete")) is not bool:
            raise ValueError("control_intervals_complete: expected bool")
        if obj.get("measurement_source", "unknown") not in {"vm", "camera", "unknown"}:
            raise ValueError("measurement_source: expected vm/camera/unknown")
        if "snapshot_at" in obj:
            _pair(obj["snapshot_at"], "snapshot_at")
    elif kind == "sf_diag_end":
        for key in ("records", "bytes"):
            _integer(obj.get(key), key, 0)
        if not isinstance(obj.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", obj["sha256"]):
            raise ValueError("sha256: expected 64 hexadecimal digits")
    else:
        _integer(obj.get("seq"), "seq", 0)
        _required_pair(obj, "begin")
        _required_pair(obj, "end")
        if kind == "sf_diag_control":
            _integer(obj.get("epoch"), "epoch", 0)
            if type(obj.get("complete")) is not bool:
                raise ValueError("complete: expected bool")
            return
        if obj.get("kind") not in {"frame", "prepare", "finish", "initialize", "calibration"}:
            raise ValueError("kind: expected frame/prepare/finish/initialize/calibration")
        if obj.get("state") not in {"pending", "committed"}:
            raise ValueError("state: expected pending/committed")
        for key in ("tid", "generation", "epoch_begin", "epoch_end", "flags"):
            _integer(obj.get(key), key, 0)
        for key in ("strength", "current_stage", "fault"):
            _integer(obj.get(key), key)
        if "rc" in obj:
            _integer(obj["rc"], "rc")
        _required_pair(obj, "current_at")
        for mask in ("invalid_mask", "stage_mask", "valid_mask"):
            if mask in obj:
                _integer(obj[mask], mask, 0)
        if "snapshot_at" in obj:
            _pair(obj["snapshot_at"], "snapshot_at")
        if "reason" in obj:
            _integer(obj["reason"], "reason", 0)
            if obj["reason"] not in REASONS or obj["path"] != REASON_PATH[obj["reason"]]:
                raise ValueError("reason/path mismatch or unknown reason")
        geometry = obj.get("geometry")
        if not isinstance(geometry, list) or len(geometry) != 4:
            raise ValueError("geometry: expected [w,h,stride,sampling]")
        for value in geometry:
            _integer(value, "geometry component", 0)
        if obj.get("path") not in PATHS:
            raise ValueError("path: invalid enum")
        stages = obj.get("stages")
        if not isinstance(stages, dict):
            raise ValueError("stages: expected object")
        for name, stage in stages.items():
            if name not in STAGES or not isinstance(stage, dict):
                raise ValueError(f"unknown/invalid stage: {name}")
            for key in ("sec", "nsec", "rc"):
                _integer(stage.get(key), f"{name}.{key}")
            _integer(stage.get("calls"), f"{name}.calls", 0)


def _bounded_integer(value, name, minimum=0, maximum=UINT32_MAX):
    _integer(value, name, minimum)
    if value > maximum:
        raise ValueError(f"{name}: expected integer <= {maximum}")


def _validate_schema2(meta, records):
    """Cross-row checks run after collection so interleaving remains supported."""
    probe = meta.get("probe")
    if not isinstance(probe, dict):
        raise ValueError("probe: expected object for schema2")
    for name, maximum in (("mode", 1), ("state", 4), ("outcome", 10),
                          ("slot", UINT32_MAX), ("prepare_baseline", UINT32_MAX),
                          ("finish_baseline", UINT32_MAX)):
        _bounded_integer(probe.get(name), "probe." + name, maximum=maximum)
    if probe["slot"] != UINT32_MAX and probe["slot"] >= PROBE_SLOTS:
        raise ValueError("probe.slot: expected frames[] index 0..127 or UINT_MAX")
    for name in ("claimed", "returned"):
        if type(probe.get(name)) is not bool:
            raise ValueError(f"probe.{name}: expected bool")
    if probe["claimed"] != (probe["state"] in (2, 3)) or probe["returned"] != (probe["state"] == 3):
        raise ValueError("probe.claimed/returned: inconsistent with state")
    # A frozen all-thread snapshot can stop after CAS but before slot/flag
    # publication. CLAIMED + UINT_MAX/no flag is a valid intermediate state.
    for name in ("pid", "window", "generation", "dropped", "clock_errors", "strength"):
        _bounded_integer(meta.get(name), name, minimum=1 if name == "pid" else 0)
    _bounded_integer(meta.get("start"), "start", maximum=(1 << 64) - 1)
    for name in ("captures", "backend_active", "inflight"):
        if name in meta:
            _bounded_integer(meta[name], name)
    attempts = []
    for record in records:
        for name in ("seq", "epoch" if record["type"] == "sf_diag_control" else "tid"):
            _bounded_integer(record[name], name)
        if record["type"] == "sf_diag_control":
            continue
        for name in ("generation", "strength", "epoch_begin", "epoch_end", "flags", "fault"):
            _bounded_integer(record[name], name)
        for name in ("invalid_mask", "stage_mask", "valid_mask", "self", "captures_begin", "captures_end"):
            if name in record:
                _bounded_integer(record[name], name)
        _bounded_integer(record["current_stage"], "current_stage")
        if record["current_stage"] != UINT32_MAX and record["current_stage"] >= len(STAGES):
            raise ValueError("current_stage: expected known stage or UINT_MAX")
        if "reason" not in record:
            raise ValueError("missing reason for schema2")
        _bounded_integer(record.get("rc"), "rc", -(1 << 31), (1 << 31) - 1)
        for value in record["geometry"]:
            _bounded_integer(value, "geometry component")
        for stage in record["stages"].values():
            for name in ("sec", "nsec", "calls"):
                _bounded_integer(stage[name], "stage." + name)
            _bounded_integer(stage["rc"], "stage.rc", -(1 << 31), (1 << 31) - 1)
        if record["flags"] & PROBE_FLAG:
            if record["kind"] != "frame":
                raise ValueError("probe flag256 is only valid on a frame")
            attempts.append(record)
    if len(attempts) > 1:
        raise ValueError("probe: more than one flag256 attempt in dump")
    if attempts and probe["mode"] != 1:
        raise ValueError("probe: flag256 attempt requires mode=1")


def _finish_validation(parsed):
    for dump in parsed.dumps.values():
        if dump.begin is None:
            dump.errors.append("missing BEGIN")
        if dump.end is None:
            dump.errors.append("missing END (truncated dump)")
        if dump.begin is None or dump.end is None:
            continue
        end = dump.end
        ordered = sorted(dump.payload)
        if end["schema"] != dump.begin["schema"]:
            dump.errors.append("schema mismatch within dump")
        records = [dump.payload[seq][0] for seq in ordered]
        try:
            if dump.begin["schema"] == 2:
                _validate_schema2(dump.begin, records)
                for name in ("records", "bytes"):
                    _bounded_integer(end[name], name)
            elif any(row.get("reason") in (11, 12) for row in records):
                raise ValueError("schema1 does not define reason11/12")
            if any("schema" in row and (type(row["schema"]) is not int
                    or row["schema"] != dump.begin["schema"]) for row in records):
                raise ValueError("schema mismatch within dump payload")
        except ValueError as exc:
            dump.errors.append(str(exc))
        if len(ordered) != end["records"]:
            dump.errors.append(f"record count mismatch: {len(ordered)} != {end['records']}")
        if any(seq != index for index, seq in enumerate(ordered)):
            dump.errors.append("sequence gap: payload seq must start at 0 and be contiguous")
        payload = [dump.payload[seq][1] for seq in ordered]
        if sum(map(len, payload)) != end["bytes"]:
            dump.errors.append("payload bytes mismatch")
        digest = hashlib.sha256()
        for raw in payload:
            digest.update(raw)
        if digest.hexdigest() != end["sha256"].lower():
            dump.errors.append("payload SHA256 mismatch")
        for obj in [end] + [dump.payload[seq][0] for seq in ordered]:
            if "build" in obj and obj["build"] != dump.begin["build"]:
                dump.errors.append("build mismatch within dump")
                break


def parse_lines(lines: Iterable[bytes | str], expected_build: int | None = None) -> ParsedLog:
    """Validate each dump independently; damaged dumps never supply statistics.

    Lines may be out of order or interleaved. Payload duplicates must be byte
    identical after stripping a recognized logging prefix. Diagnostics with a
    malformed prefix/JSON are errors, not silently discarded as unrelated logs.
    """
    result = ParsedLog()
    for line_number, line in enumerate(lines, 1):
        raw = line.encode("utf-8") if isinstance(line, str) else line
        marker = b"sf_diag_" in raw
        if not marker:
            continue
        dump = None
        try:
            # Try to attribute even a truncated JSON row to its dump.
            hint = ID_HINT.search(raw.decode("utf-8", errors="replace"))
            if hint:
                dump_id = json.loads(hint.group(1))
                if dump_id:
                    dump = result.dumps.setdefault(dump_id, Dump(dump_id))
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError(f"line exceeds {MAX_LINE_BYTES} UTF-8 bytes including LF")
            if not raw.endswith(b"\n"):
                raise ValueError("incomplete diagnostic line (missing LF)")
            body = raw[:-1]
            if not body.startswith(b"{"):
                prefix = PREFIX.match(body)
                if not prefix:
                    raise ValueError("diagnostic JSON must start at line start or after a simple log prefix")
                body = body[prefix.end():]
            obj = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
            if not isinstance(obj, dict):
                raise ValueError("diagnostic row must be an object")
            kind = obj.get("type")
            if kind not in TYPES:
                # A recognized diagnostic namespace typo is never ignored.
                if isinstance(kind, str) and kind.startswith("sf_diag_"):
                    raise ValueError(f"unknown diagnostic type: {kind}")
                if dump is not None:
                    raise ValueError("diagnostic marker without a valid type")
                continue
            _string(obj.get("dump_id"), "dump_id")
            dump = result.dumps.setdefault(obj["dump_id"], Dump(obj["dump_id"]))
            _validate(obj, expected_build)
            if kind in {"sf_diag_begin", "sf_diag_end"}:
                attr = "begin" if kind == "sf_diag_begin" else "end"
                prior = getattr(dump, attr)
                if prior is not None and prior != obj:
                    raise ValueError(f"conflicting duplicate {attr.upper()}")
                setattr(dump, attr, obj)
            else:
                seq = obj["seq"]
                payload = (obj, body + b"\n")
                if seq in dump.payload and dump.payload[seq][1] != payload[1]:
                    raise ValueError(f"conflicting duplicate seq {seq}")
                dump.payload[seq] = payload
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            errors = dump.errors if dump is not None else result.errors
            errors.append(f"line {line_number}: {exc}")
    if not result.dumps:
        result.errors.append("no smartfilter diagnostic dumps found")
    _finish_validation(result)
    return result


def parse_log(path: Path | str, expected_build: int | None = None) -> ParsedLog:
    with Path(path).open("rb") as stream:
        return parse_lines(stream, expected_build)


def _stats(values):
    """Integer nanoseconds, nearest-rank quantiles; never sum percentiles."""
    ordered = sorted(values)
    if not ordered:
        return None
    def percentile(percent):
        return ordered[max(0, (len(ordered) * percent + 99) // 100 - 1)]
    return {"n": len(ordered), "p50_ns": percentile(50), "p90_ns": percentile(90),
            "p95_ns": percentile(95), "max_ns": ordered[-1]}


def _key(meta, record, cohort):
    return {"pid": meta["pid"], "start": meta["start"], "build": meta["build"],
            "window": meta["window"], "look_id": meta["look_id"],
            "generation": record["generation"], "strength": record["strength"],
            "geometry": record["geometry"], "path": record["path"],
            "measurement_source": meta.get("measurement_source", "unknown"),
            "kind": record["kind"], "cohort": cohort, "reason": record.get("reason")}


def _add_group(groups, key, dump_id, record, wall, calibration=False, entry_interval=None):
    token = json.dumps(key, sort_keys=True, ensure_ascii=True)
    group = groups.setdefault(token, {"key": key, "dump_ids": set(), "count": 0,
                                     "samples": {}, "coverage": {}})
    group["dump_ids"].add(dump_id)
    group["count"] += 1
    if wall is not None:
        group["samples"].setdefault("wall", []).append(wall)
    if entry_interval is not None:
        group["samples"].setdefault("entry_interval", []).append(entry_interval)
    for name, stage in record["stages"].items():
        if (name == "calibration") != calibration:
            continue
        coverage = group["coverage"].setdefault(name, {"present": 0, "zero_calls": 0,
                                                        "invalid_duration": 0, "calls": 0})
        coverage["present"] += 1
        coverage["calls"] += stage["calls"]
        if stage["calls"] == 0:
            coverage["zero_calls"] += 1
        elif (_duration(stage) is None
              or record.get("invalid_mask", 0) & (1 << STAGES.index(name))
              or not record.get("valid_mask", (1 << len(STAGES)) - 1) & (1 << STAGES.index(name))
              or not record.get("stage_mask", (1 << len(STAGES)) - 1) & (1 << STAGES.index(name))):
            coverage["invalid_duration"] += 1
        else:
            group["samples"].setdefault(name, []).append(_duration(stage))


def _final_groups(groups):
    result = []
    for token in sorted(groups):
        group = groups[token]
        result.append({"key": group["key"], "dump_ids": sorted(group["dump_ids"]),
                       "count": group["count"],
                       "metrics": {name: _stats(values) for name, values in group["samples"].items()},
                       "stage_coverage": group["coverage"]})
    return result


def _record_identity(dump_id, record, schema):
    return {"dump_id": dump_id, "seq": record["seq"], "kind": record["kind"],
            "tid": record["tid"], "path": record["path"], "fault": record["fault"],
            "flags": record["flags"], "unknown_flags": record["flags"] & ~(511 if schema == 2 else 131),
            "reason": record.get("reason"), "reason_name": REASONS.get(record.get("reason")),
            "stage_rc": {name: stage["rc"] for name, stage in record["stages"].items()},
            **{name: record[name] for name in ("rc", "invalid_mask", "stage_mask", "valid_mask")
               if name in record}}


def _pending_entry(identity, record, meta):
    entry = {**identity, "current_stage": record["current_stage"],
             "current_stage_name": STAGES[record["current_stage"]]
             if 0 <= record["current_stage"] < len(STAGES) else None,
             "current_at": record["current_at"]}
    snapshot_pair = record.get("snapshot_at", meta.get("snapshot_at"))
    snapshot = _time(snapshot_pair)
    begin, current = _time(record["begin"]), _time(record["current_at"])
    if snapshot_pair is not None:
        entry["snapshot_at"] = snapshot_pair
    if (snapshot is not None and begin is not None and snapshot >= begin
            and (current is None or snapshot >= current) and not record["flags"] & 1):
        entry["lower_bound_ns"] = snapshot - begin
    return entry


def _record_issues(record, controls, coverage_complete):
    reasons = []
    span = _span(record)
    if record["flags"] & 1 or span is None:
        reasons.append("invalid_clock")
    if record["fault"]:
        reasons.append("fault")
    lifecycle_status = record["kind"] in {"prepare", "finish"}
    rc_error = (lambda rc: rc < 0) if lifecycle_status else (lambda rc: rc != 0)
    if rc_error(record.get("rc", 0)):
        reasons.append("record_error")
    if any(rc_error(stage["rc"]) for stage in record["stages"].values()):
        reasons.append("stage_error")
    if record["kind"] == "frame":
        if record["path"] == "unknown":
            reasons.append("unknown_path")
        if record["epoch_begin"] != record["epoch_end"] or record["flags"] & 128:
            reasons.append("control_epoch_changed")
        if record.get("reason") in {4, 5, 6, 7, 10, 11, 12}:
            reasons.append("nonsteady_" + REASONS[record["reason"]])
        if record["flags"] & 2:
            reasons.append("first_after_control")
        if not coverage_complete:
            reasons.append("control_coverage_incomplete")
        # Inclusive boundary exclusion is intentionally conservative.
        if span is not None and any(_span(c) is not None and
                span[0] <= _span(c)[1] and _span(c)[0] <= span[1] for c in controls):
            reasons.append("control_overlap")
    return reasons


def _single_record(dump_id, record, meta, controls, coverage_complete):
    """One observation, including failures; never percentiles or stage sums."""
    pending = record["state"] == "pending"
    span = _span(record)
    result = {**_record_identity(dump_id, record, meta["schema"]),
              "state": record["state"], "generation": record["generation"],
              "strength": record["strength"], "geometry": record["geometry"],
              "epoch_begin": record["epoch_begin"], "epoch_end": record["epoch_end"],
              "begin": record["begin"], "end": record["end"],
              "labels": [name for bit, name in ((2, "FIRST"), (4, "COLD"), (8, "SELECT"))
                         if record["flags"] & bit],
              "wall_ns": span[1] - span[0] if not pending and span is not None
                         and not record["flags"] & 1 else None,
              "issues": _record_issues(record, controls, coverage_complete), "stages": {}}
    if pending:
        # Partial stage accumulators are not completed attempt durations.
        result.update(_pending_entry({}, record, meta))
        if not record["flags"] & 1 and _time(record["begin"]) is not None:
            result["issues"] = [issue for issue in result["issues"] if issue != "invalid_clock"]
        result["issues"].append("pending")
    for name, stage in record["stages"].items():
        bit = 1 << STAGES.index(name)
        valid = (not pending and not record["flags"] & 1
                 and stage["calls"] > 0 and _duration(stage) is not None
                 and not record.get("invalid_mask", 0) & bit
                 and record.get("valid_mask", (1 << len(STAGES)) - 1) & bit
                 and record.get("stage_mask", (1 << len(STAGES)) - 1) & bit)
        result["stages"][name] = {**stage, "duration_ns": _duration(stage) if valid else None}
    for bit, name in ((32, "restore_error"), (64, "unlock_error")):
        if record["flags"] & bit:
            result["issues"].append(name)
    return result


def _probe_attempt(dump_id, meta, records, controls, coverage_complete):
    probe = meta["probe"]
    attempt = next((r for r in records if r["kind"] == "frame" and r["flags"] & PROBE_FLAG), None)
    detail = (_single_record(dump_id, attempt, meta, controls, coverage_complete)
              if attempt is not None else None)
    if probe["state"] == 4:
        status = "blocked"
    elif detail is not None and detail["state"] == "pending":
        status = "pending"
    elif probe["returned"]:
        status = "returned_success" if probe["outcome"] == 1 else "returned_nonsuccess"
        if detail is not None and status == "returned_success" and (
                detail["fault"] or detail.get("rc", 0) or detail["path"] != "render"
                or any(detail["stage_rc"].values()) or detail["flags"] & (32 | 64)):
            status = "returned_error"
    elif probe["claimed"]:
        status = "committed_awaiting_return" if detail is not None else "claimed_intermediate"
    elif probe["mode"] == 0:
        status = "off"
    else:
        status = "not_attempted"
    issues = []
    if probe["claimed"] and detail is None:
        issues.append("attempt_record_unavailable")
    if probe["returned"] and probe["outcome"] != 1:
        issues.append("returned_nonsuccess")
    if probe["returned"] and probe["outcome"] == 1 and detail is None:
        # Exported SUCCESS is not a measured success without the record.
        status = "returned_record_unavailable"
    related = [_single_record(dump_id, r, meta, controls, coverage_complete) for r in records
               if r["kind"] == "initialize" and r["generation"] == meta["generation"]]
    return {"dump_id": dump_id, "key": {name: meta[name] for name in
            ("pid", "start", "build", "window", "generation", "look_id", "strength")},
            "measurement_source": meta.get("measurement_source", "unknown"),
            "runtime": {name: meta[name] for name in ("backend_active", "inflight", "captures", "frozen") if name in meta},
            "probe": probe, "state_name": PROBE_STATES[probe["state"]],
            "outcome_name": PROBE_OUTCOMES[probe["outcome"]],
            "attempted": probe["claimed"] or attempt is not None,
            "attempt_record_present": attempt is not None, "status": status,
            "issues": issues, "record": detail, "initialize_records": related,
            "initialize_association": "same dump and generation only; no nesting or sum implied"}


def analyze(parsed: ParsedLog, entry_intervals: bool = False) -> dict:
    """Return JSON-serializable totals, stage distributions, and exclusions.

    Only validated dumps enter any numeric timing table. Successful frame
    cohorts are cold if initialize/select actually ran, otherwise steady.
    Passthrough/fallback are never renamed render. Pending has no durations.
    Optional entry_intervals adds same-TID/epoch grouping and record-entry gaps,
    never node queue latency or display/sensor FPS. Sequence order is used after
    payload validation; nonmonotonic begin times break the pairing chain.
    """
    frames, lifecycle, calibration = {}, {}, {}
    dumps, excluded, pending, record_metadata, probe_attempts = [], [], [], [], []
    outcomes, faults = Counter(), Counter()
    for dump_id, dump in sorted(parsed.dumps.items()):
        status = {"dump_id": dump_id, "valid": dump.valid, "errors": dump.errors,
                  "metadata": dump.begin, "payload_rows": len(dump.payload)}
        dumps.append(status)
        if not dump.valid:
            continue
        meta = dump.begin
        controls = [row for row, _ in dump.payload.values() if row["type"] == "sf_diag_control"]
        coverage_complete = meta["control_intervals_complete"] and all(
            c["complete"] and _span(c) is not None for c in controls)
        status["controls"] = controls
        status["warnings"] = []
        if meta["dropped"]:
            status["warnings"].append("dropped records: distributions cover retained records only")
        if meta["clock_errors"]:
            status["warnings"].append("clock errors reported; invalid samples are excluded")
        records = [dump.payload[seq][0] for seq in sorted(dump.payload)
                   if dump.payload[seq][0]["type"] == "sf_diag_record"]
        probe_mode = meta["schema"] == 2 and meta["probe"]["mode"] == 1
        if meta["schema"] == 2:
            probe_attempts.append(_probe_attempt(dump_id, meta, records, controls, coverage_complete))
        previous_entries = {}
        for seq in sorted(dump.payload):
            record = dump.payload[seq][0]
            if record["type"] != "sf_diag_record":
                continue
            identity = _record_identity(dump_id, record, meta["schema"])
            # Pop first: any pending, failed, polluted or non-frame record on
            # this TID breaks the chain unless a successful frame restores it.
            previous = previous_entries.pop(record["tid"], None) if entry_intervals else None
            record_metadata.append(identity)
            faults[str(record["fault"])] += 1
            if record["state"] == "pending":
                outcomes["pending"] += 1
                pending.append(_pending_entry(identity, record, meta))
                continue
            if meta["schema"] == 2 and record["flags"] & PROBE_FLAG:
                outcomes["probe_attempt_committed"] += 1
                continue
            if probe_mode and record["kind"] == "initialize" and record["generation"] == meta["generation"]:
                # Create/ensure has its own observation in the probe report;
                # it is not part of the one frame wall or steady distributions.
                outcomes["probe_initialize_committed"] += 1
                continue
            span = _span(record)
            reasons = _record_issues(record, controls, coverage_complete)
            if reasons:
                if record["path"] == "fallback" and any(r in reasons for r in ("fault", "record_error", "stage_error")):
                    outcome = "fallback_error"
                elif record.get("reason") in {4, 5, 6, 7, 10, 11, 12}:
                    outcome = REASONS[record["reason"]]
                elif "fault" in reasons:
                    outcome = "fault"
                elif "stage_error" in reasons:
                    outcome = "stage_error"
                elif "record_error" in reasons:
                    outcome = "record_error"
                else:
                    outcome = "excluded"
                outcomes[outcome] += 1
                excluded.append({**identity, "outcome": outcome, "reasons": reasons})
                continue
            cold = any(record["stages"].get(name, {}).get("calls", 0) > 0
                       for name in ("initialize", "select"))
            if meta["schema"] == 2:
                cold = cold or bool(record["flags"] & (4 | 8))
            cohort = "cold" if cold else "steady"
            key = _key(meta, record, cohort)
            entry_interval = None
            if entry_intervals and record["kind"] == "frame":
                key.update(tid=record["tid"], epoch=record["epoch_begin"])
                signature = json.dumps({k: v for k, v in key.items() if k != "cohort"}, sort_keys=True)
                if (meta["dropped"] == 0 and previous is not None
                        and previous[0] == signature and span[0] > previous[1]
                        and not any(_span(c) is not None and previous[1] <= _span(c)[1]
                                    and _span(c)[0] <= span[0] for c in controls)):
                    entry_interval = span[0] - previous[1]
                previous_entries[record["tid"]] = (signature, span[0])
            target = (frames if record["kind"] == "frame" else calibration
                      if record["kind"] == "calibration" else lifecycle)
            _add_group(target, key, dump_id, record, span[1] - span[0],
                       calibration=record["kind"] == "calibration", entry_interval=entry_interval)
            outcomes[f"{record['kind']}_{record['path']}_success"] += 1
            if record["kind"] != "calibration" and record["stages"].get("calibration", {}).get("calls", 0) > 0:
                _add_group(calibration, key, dump_id, record, None, calibration=True)
    return {"schema": max((dump.begin["schema"] for dump in parsed.dumps.values() if dump.begin), default=1),
            "ok": parsed.ok, "errors": parsed.errors, "dumps": dumps,
            "entry_intervals_enabled": entry_intervals,
            "outcomes": dict(sorted(outcomes.items())), "fault_counts": dict(sorted(faults.items())),
            "frame_groups": _final_groups(frames), "lifecycle_groups": _final_groups(lifecycle),
            "calibration_groups": _final_groups(calibration), "pending": pending,
            "record_metadata": record_metadata, "probe_attempts": probe_attempts,
            "excluded_records": excluded,
            "notes": ["Durations are integer nanoseconds; quantiles use nearest rank.",
                      "measurement_source is exporter-declared, not independent proof of camera measurement.",
                      "No screen FPS is inferred from entry intervals.",
                      "Optional entry_interval is a same-TID record-entry interval from reserve begin, not exact preview-node queue latency or sensor/display FPS; its sample count is independent.",
                      "Entry pairing breaks on same-TID pending/failed/polluted/non-frame records, changed configuration/epoch, non-increasing timestamps, or a control interval between endpoints; dropped>0 disables interval samples because missing intermediates cannot be ruled out.",
                      "Stage totals are accumulated per record, not per-call latency.",
                      "No residual or leaf sum: schema has no per-call intervals to prove non-overlap.",
                      "prepare/finish are lifecycle timings, not disk-write latency.",
                      "prepare/finish accept nonnegative wrapper rc (Finish=1 is success); raw record/stage rc are retained, not proof of disk completion.",
                      "Missing stages have no sample; calls=0 is tracked but has no duration sample.",
                      "Pending has no completed durations; lower bounds require a valid explicit snapshot_at.",
                      "Busy is reason=6, not an errno or fault value; reasons 4/5/6/7/10 are nonsteady.",
                      "Schema2 probe_attempts are single observations, never included in steady/cold quantiles or entry intervals; FIRST/COLD/SELECT are retained as labels.",
                      "Probe wall and individual stages (including nested preview/effect/ensure) are not added, nor is Create initialize added to frame wall; initialize association is only same dump/generation.",
                      "Probe RETURNED is not SUCCESS; exported outcome, record/stage rc, faults, restore/unlock and control pollution remain explicit, even with fallback rc=0.",
                      "Probe CLAIMED without a record/slot can be a frozen publication intermediate; no measured success is inferred. slot is a frames[] index, not payload seq.",
                      "Schema2 reason11/12 mean probe_exhausted/probe_blocked, not hardware busy (reason6).",
                      "Only valid dumps supply timings; errors in any dump make CLI exit nonzero."]}


def _escape(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("`", "'")


def _label(key):
    return _escape(json.dumps(key, ensure_ascii=False, separators=(",", ":")))


def _row(label, metric, stats):
    return f"| {label} | {_escape(metric)} | {stats['n']} | {stats['p50_ns']} | {stats['p90_ns']} | {stats['p95_ns']} | {stats['max_ns']} |"


def to_markdown(report: dict) -> str:
    lines = ["# Smartfilter RAM timing", "", f"校验状态：{'通过' if report['ok'] else '失败（CLI 非零）'}", "",
             "仅展示校验通过 dump 的统计。vm/unknown 不表示相机实测；camera 也是导出方声明。", ""]
    for error in report["errors"]:
        lines.append(f"- ERROR: {_escape(error)}")
    for dump in report["dumps"]:
        lines.append(f"- dump `{_escape(dump['dump_id'])}`: {'valid' if dump['valid'] else 'INVALID'}")
        for error in dump["errors"]:
            lines.append(f"  - ERROR: {_escape(error)}")
        for warning in dump.get("warnings", []):
            lines.append(f"  - WARNING: {_escape(warning)}")
    header = ["| 分组 | 指标 | n | p50 ns | p90 ns | p95 ns | max ns |",
              "|---|---|---:|---:|---:|---:|---:|"]
    lines += ["", "## 1. Frame totals（cold / steady、path 独立）", ""] + header
    for group in report["frame_groups"]:
        for metric in ("wall", "preview", "effect", "entry_interval"):
            if metric in group["metrics"]:
                lines.append(_row(_label(group["key"]), metric, group["metrics"][metric]))
    lines += ["", "## 2. Individual stages（不相加，不声称 leaf 非重叠）", ""] + header
    for group in report["frame_groups"]:
        for metric, stats in group["metrics"].items():
            if metric not in {"wall", "preview", "effect", "entry_interval"}:
                lines.append(_row(_label(group["key"]), metric, stats))
    lines += ["", "## 3. Prepare / finish / initialize 与 calibration（分别列示，非磁盘写入时长）", ""] + header
    for section in ("lifecycle_groups", "calibration_groups"):
        for group in report[section]:
            for metric, stats in group["metrics"].items():
                lines.append(_row(_label(group["key"]), metric, stats))
    if report.get("probe_attempts"):
        lines += ["", "## 4. Probe attempts（一次观测，不纳入 steady p95 / FPS）", "",
                  "wall 与分段独立展示，不相加嵌套阶段；FIRST/COLD/SELECT 不是 steady 样本。", ""]
        for attempt in report["probe_attempts"]:
            lines += [f"### dump `{_escape(attempt['dump_id'])}`", "",
                      f"- status: `{_escape(attempt['status'])}`; state: `{attempt['state_name']}`; outcome: `{attempt['outcome_name']}`",
                      f"- probe: `{_escape(json.dumps(attempt['probe'], ensure_ascii=False))}`",
                      f"- runtime: `{_escape(json.dumps(attempt['runtime'], ensure_ascii=False))}`",
                      f"- identity/source: `{_escape(json.dumps(attempt['key'], ensure_ascii=False))}` / `{_escape(attempt['measurement_source'])}`",
                      f"- issues: `{_escape(json.dumps(attempt['issues']))}`",
                      "- Create initialize 仅同 dump/generation 关联，不证明嵌套；其 wall 不加入 attempt wall。"]
            observations = ([] if attempt["record"] is None else [("attempt", attempt["record"])])
            observations += [("Create initialize", row) for row in attempt["initialize_records"]]
            if attempt["record"] is None:
                lines.append("- 无 flag256 记录：未尝试、CLAIMED 中间态或记录不可用，不能推断一次成功耗时。")
            for label, row in observations:
                lines += [f"- {_escape(label)} seq={row['seq']}: state={row['state']}, rc={row.get('rc')}, fault={row['fault']}, flags={row['flags']}, labels={','.join(row['labels']) or '-'}",
                          f"  - issues: `{_escape(json.dumps(row['issues']))}`",
                          f"  - wall ns: {row['wall_ns'] if row['wall_ns'] is not None else 'unavailable（pending/invalid，无完成时长）'}"]
                if "lower_bound_ns" in row:
                    lines.append(f"  - pending lower_bound ns: {row['lower_bound_ns']}（非完成时长）")
                lines += ["", "| 独立分段 | calls | rc | duration ns |", "|---|---:|---:|---:|"]
                for name, stage in row["stages"].items():
                    duration = stage["duration_ns"]
                    lines.append(f"| {_escape(name)} | {stage['calls']} | {stage['rc']} | {duration if duration is not None else 'unavailable'} |")
                lines.append("")
    lines += ["", "## 排除项与 pending", "", f"- outcomes: `{_escape(json.dumps(report['outcomes'], ensure_ascii=False))}`"]
    for record in report["pending"]:
        lines.append(f"- pending: `{_escape(json.dumps(record, ensure_ascii=False))}`（无完成时长）")
    for record in report["excluded_records"]:
        lines.append(f"- excluded: `{_escape(json.dumps(record, ensure_ascii=False))}`")
    lines += ["", "## 限制", ""] + [f"- {_escape(note)}" for note in report["notes"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="mixed log containing complete schema-1/2 NDJSON lines")
    parser.add_argument("--json", dest="json_path", type=Path, help="write JSON report")
    parser.add_argument("--markdown", dest="markdown_path", type=Path, help="write Markdown report")
    parser.add_argument("--build", type=lambda value: int(value, 0),
                        help="require this exact nonzero uint32 build (decimal or 0x)")
    parser.add_argument("--entry-interval", action="store_true",
                        help="report optional same-TID record-entry intervals (not FPS or node queue latency)")
    args = parser.parse_args(argv)
    try:
        paths = [p.resolve() for p in (args.log, args.json_path, args.markdown_path) if p is not None]
        if len(set(paths)) != len(paths):
            raise ValueError("input and output paths must be distinct")
        report = analyze(parse_log(args.log, expected_build=args.build), entry_intervals=args.entry_interval)
        if args.json_path is not None:
            args.json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown = to_markdown(report)
        if args.markdown_path is not None:
            args.markdown_path.write_text(markdown, encoding="utf-8")
        if args.json_path is None and args.markdown_path is None:
            sys.stdout.write(markdown)
        if not report["ok"]:
            for error in report["errors"]:
                print(f"ERROR: {error}", file=sys.stderr)
            for dump in report["dumps"]:
                for error in dump["errors"]:
                    print(f"ERROR [{dump['dump_id']}]: {error}", file=sys.stderr)
        return 0 if report["ok"] else 1
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
