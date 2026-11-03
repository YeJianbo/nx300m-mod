#!/usr/bin/env python3
"""Host-only synthetic fixtures. These tests make no camera-performance claim.

Run: python -I -B tools/test_smartfilter_timing.py
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).with_name("analyze_smartfilter_timing.py")
SPEC = importlib.util.spec_from_file_location("smartfilter_timing_under_test", TOOL)
sf = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sf
SPEC.loader.exec_module(sf)
BUILD = 0x30000001


def line(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def stage(ns=0, calls=1, rc=0):
    return {"sec": ns // sf.NS, "nsec": ns % sf.NS, "calls": calls, "rc": rc}


def record(seq=0, dump_id="d", **changes):
    obj = {"type": "sf_diag_record", "dump_id": dump_id, "seq": seq,
           "kind": "frame", "state": "committed", "tid": 21, "generation": 1,
           "strength": 100, "epoch_begin": 2, "epoch_end": 2,
           "geometry": [640, 480, 1280, 1], "begin": [10, 0], "end": [10, 100],
           "current_stage": 0, "current_at": [10, 0], "flags": 0, "path": "render",
           "reason": 1, "fault": 0, "stages": {"preview": stage(100), "effect": stage(80)}}
    obj.update(changes)
    return obj


def entry_frame(seq, at_ns, **changes):
    return record(seq, begin=[10 + at_ns // sf.NS, at_ns % sf.NS],
                  end=[10 + (at_ns + 5) // sf.NS, (at_ns + 5) % sf.NS], **changes)


def control(seq=0, dump_id="d", **changes):
    obj = {"type": "sf_diag_control", "dump_id": dump_id, "seq": seq,
           "epoch": 3, "begin": [9, 0], "end": [9, 100], "complete": True}
    obj.update(changes)
    return obj


def bundle(records=None, dump_id="d", metadata=None, ending=None, raw_payload=None):
    records = [record(dump_id=dump_id)] if records is None else records
    begin = {"type": "sf_diag_begin", "schema": 1, "dump_id": dump_id,
             "pid": 123, "start": 4567, "build": BUILD, "window": "steady-1",
             "look_id": 2, "generation": 1, "strength": 100,
             "clock": "CLOCK_MONOTONIC", "unit": "sec_nsec", "dropped": 0,
             "clock_errors": 0, "control_intervals_complete": True, "measurement_source": "vm"}
    begin.update(metadata or {})
    payload = ([line(obj) for obj in sorted(records, key=lambda obj: obj["seq"])]
               if raw_payload is None else raw_payload)
    joined = b"".join(payload)
    end = {"type": "sf_diag_end", "schema": 1, "dump_id": dump_id,
           "records": len(payload), "bytes": len(joined), "sha256": hashlib.sha256(joined).hexdigest()}
    end.update(ending or {})
    return [line(begin), *payload, line(end)]


def probe_metadata(state=3, **changes):
    probe = {"mode": 1, "state": state, "slot": 0 if state in (2, 3) else sf.UINT32_MAX,
             "outcome": 1 if state == 3 else 0, "claimed": state in (2, 3),
             "returned": state == 3, "prepare_baseline": 0, "finish_baseline": 0}
    probe.update(changes)
    return probe


def record2(seq=0, dump_id="d", **changes):
    return record(seq, dump_id, **{"flags": sf.PROBE_FLAG, "rc": 0, **changes})


def bundle2(records=None, dump_id="d", metadata=None, ending=None, raw_payload=None):
    meta = {"schema": 2, "window": 1, "probe": probe_metadata()}
    meta.update(metadata or {})
    return bundle([record2(dump_id=dump_id)] if records is None else records,
                  dump_id, metadata=meta, ending={"schema": 2, **(ending or {})}, raw_payload=raw_payload)


def analyze(rows, expected_build=None, entry_intervals=False):
    return sf.analyze(sf.parse_lines(rows, expected_build=expected_build), entry_intervals=entry_intervals)


class TimingTest(unittest.TestCase):
    def assert_bad(self, rows, text):
        report = analyze(rows)
        self.assertFalse(report["ok"])
        errors = report["errors"] + [error for dump in report["dumps"] for error in dump["errors"]]
        self.assertTrue(any(text in error for error in errors), errors)
        self.assertEqual(report["frame_groups"], [])
        self.assertEqual(report["lifecycle_groups"], [])
        self.assertEqual(report["calibration_groups"], [])
        return report

    def test_normal_three_groups_and_nearest_rank(self):
        rows = []
        for group in range(3):
            dump_id = f"group-{group}"
            records = [record(seq, dump_id, end=[10, (seq + 1) * 10],
                              stages={"preview": stage((seq + 1) * 10),
                                      "effect": stage(seq + 1), "iq_process": stage(seq + 1)})
                       for seq in range(20)]
            rows += bundle(records, dump_id, metadata={"window": group})
        report = analyze(rows, expected_build=BUILD)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["frame_groups"]), 3)
        self.assertEqual(report["outcomes"]["frame_render_success"], 60)
        for group in report["frame_groups"]:
            self.assertEqual(group["metrics"]["preview"],
                             {"n": 20, "p50_ns": 100, "p90_ns": 180, "p95_ns": 190, "max_ns": 200})
            self.assertEqual(group["key"]["measurement_source"], "vm")
        markdown = sf.to_markdown(report)
        self.assertIn("## 1.", markdown)
        self.assertIn("## 2.", markdown)
        self.assertIn("## 3.", markdown)
        self.assertIn("vm/unknown", markdown)
        self.assertNotIn("fps", json.dumps(report["frame_groups"]).lower())

    def test_mixed_interleaved_out_of_order_and_exact_duplicates(self):
        rows = []
        for name in ("a", "b", "c"):
            rows += bundle([record(i, name) for i in range(3)], name)
        rows += [rows[1], rows[0], rows[4]]
        random.Random(17).shuffle(rows)
        rows.insert(2, b"fn: unrelated camera message\n")
        rows.insert(5, b'{"type":"other","message":"hello"}\n')
        report = analyze(rows)
        self.assertTrue(report["ok"])
        self.assertEqual(report["outcomes"]["frame_render_success"], 9)
        self.assertEqual(sum(dump["payload_rows"] for dump in report["dumps"]), 9)

    def test_accepted_simple_prefix_preserves_original_json_hash(self):
        rows = bundle()
        prefixed = [b"[12:34:56] [fn] timing: " + row for row in rows]
        self.assertTrue(analyze(prefixed)["ok"])
        self.assertTrue(analyze([b"fn: " + row for row in rows])["ok"])

    def test_damaged_prefix_not_swallowed(self):
        rows = bundle()
        rows[1] = b"arbitrary message before " + rows[1]
        self.assert_bad(rows, "simple log prefix")

    def test_missing_end(self):
        self.assert_bad(bundle()[:-1], "missing END")

    def test_missing_begin(self):
        self.assert_bad(bundle()[1:], "missing BEGIN")

    def test_missing_lf_is_truncation_even_if_json_is_complete(self):
        rows = bundle()
        rows[-1] = rows[-1].rstrip(b"\n")
        self.assert_bad(rows, "missing LF")

    def test_truncated_payload_is_not_noise(self):
        rows = bundle()
        rows[1] = rows[1][:-20] + b"\n"
        self.assert_bad(rows, "line 2")

    def test_checksum_error(self):
        self.assert_bad(bundle(ending={"sha256": "0" * 64}), "SHA256")

    def test_bytes_error(self):
        self.assert_bad(bundle(ending={"bytes": 0}), "bytes mismatch")

    def test_count_error(self):
        self.assert_bad(bundle(ending={"records": 9}), "count mismatch")

    def test_sequence_gap(self):
        self.assert_bad(bundle([record(0), record(2)]), "sequence gap")

    def test_sequence_must_start_at_zero(self):
        self.assert_bad(bundle([record(1)]), "sequence gap")

    def test_conflicting_duplicate_record(self):
        rows = bundle()
        rows.insert(2, line(record(end=[10, 101])))
        self.assert_bad(rows, "conflicting duplicate seq")

    def test_semantically_equal_but_byte_different_duplicate_is_conflict(self):
        rows = bundle()
        rows.insert(2, json.dumps(record(), indent=None).encode() + b"\n")
        self.assert_bad(rows, "conflicting duplicate seq")

    def test_conflicting_duplicate_begin_and_end(self):
        for index, field, value in ((0, "build", BUILD + 1), (-1, "records", 999)):
            with self.subTest(field=field):
                rows = bundle()
                changed = json.loads(rows[index])
                changed[field] = value
                rows.append(line(changed))
                self.assert_bad(rows, "conflicting duplicate")

    def test_record_and_control_share_sequence(self):
        rows = bundle([record(0), control(1), record(2)])
        self.assertTrue(analyze(rows)["ok"])
        rows.insert(2, line(control(0)))
        self.assert_bad(rows, "conflicting duplicate seq")

    def test_one_bad_dump_does_not_silently_disappear_or_poison_good(self):
        rows = bundle([record(0, "good")], "good") + bundle([record(0, "bad")], "bad")[:-1]
        report = analyze(rows)
        self.assertFalse(report["ok"])
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)
        self.assertEqual(report["frame_groups"][0]["dump_ids"], ["good"])
        self.assertEqual({d["dump_id"]: d["valid"] for d in report["dumps"]}, {"bad": False, "good": True})

    def test_schema_and_build_validation(self):
        self.assert_bad(bundle(metadata={"schema": 3}), "schema")
        self.assert_bad(bundle(ending={"schema": 3}), "schema")
        for value in (None, "native", 0, -1, True, 2**32):
            with self.subTest(value=value):
                self.assert_bad(bundle(metadata={"build": value}), "build")
        report = analyze(bundle(), expected_build=BUILD + 1)
        self.assertFalse(report["ok"])
        self.assertIn("build mismatch", " ".join(report["dumps"][0]["errors"]))
        self.assertEqual(report["frame_groups"], [])

    def test_optional_payload_build_cannot_disagree(self):
        self.assert_bad(bundle([record(build=BUILD + 1)]), "build mismatch within dump")
        self.assert_bad(bundle(ending={"build": BUILD + 1}), "build mismatch within dump")

    def test_required_metadata_and_exact_types(self):
        for field, value in (("pid", True), ("generation", "1"),
                             ("control_intervals_complete", 1), ("clock", "REALTIME"),
                             ("unit", "ns"), ("measurement_source", "simulator")):
            with self.subTest(field=field):
                self.assert_bad(bundle(metadata={field: value}), field.split("_")[0])

    def test_oversized_line_rejected(self):
        rows = bundle()
        rows[1] = rows[1][:-1] + b" " * 4096 + b"\n"
        self.assert_bad(rows, "exceeds")

    def test_utf8_and_trailing_whitespace_hash_are_exact(self):
        payload = line(record(comment="试验"))[:-1] + b" \t\n"
        self.assertTrue(analyze(bundle(raw_payload=[payload]))["ok"])
        rows = bundle(raw_payload=[payload])
        rows[1] = payload.rstrip() + b"\n"
        self.assert_bad(rows, "bytes mismatch")

    def test_crlf_hash_includes_original_cr(self):
        payload = line(record())[:-1] + b"\r\n"
        self.assertTrue(analyze(bundle(raw_payload=[payload]))["ok"])

    def test_json_duplicate_keys_and_nan_rejected(self):
        for raw in (line(record()).replace(b'"seq":0', b'"seq":0,"seq":0'),
                    line(record()).replace(b'"fault":0', b'"fault":NaN')):
            with self.subTest(raw=raw):
                self.assert_bad(bundle(raw_payload=[raw]), "line 2")

    def test_invalid_utf8_and_unknown_stage_rejected(self):
        raw = line(record(comment="x")).replace(b'"x"', b'"\xff"')
        self.assert_bad(bundle(raw_payload=[raw]), "line 2")
        self.assert_bad(bundle([record(stages={"invented": stage(2)})]), "unknown/invalid stage")

    def test_unknown_diagnostic_namespace_not_ignored(self):
        rows = bundle()
        rows.insert(1, line({"type": "sf_diag_reccord", "dump_id": "d"}))
        self.assert_bad(rows, "unknown diagnostic type")

    def test_negative_absolute_span_and_clock_invalid(self):
        rows = bundle([record(0, end=[9, 900]), record(1, flags=1),
                       record(2, begin=[-1, 0]), record(3, end=[10, sf.NS]), record(4)])
        report = analyze(rows)
        self.assertTrue(report["ok"])
        self.assertEqual(report["outcomes"]["excluded"], 4)
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)
        self.assertEqual(report["frame_groups"][0]["count"], 1)
        self.assertTrue(all("invalid_clock" in entry["reasons"] for entry in report["excluded_records"]))

    def test_null_clock_is_not_zero_timestamp(self):
        report = analyze(bundle([record(0, begin=None), record(1, end=None), record(2, current_at=None)]))
        self.assertTrue(report["ok"])
        self.assertEqual(report["outcomes"]["excluded"], 2)
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)

    def test_pending_has_no_completed_duration(self):
        obj = record(state="pending", end=None, current_stage=21, current_at=[10, 90],
                     stages={"preview": stage(99999)})
        report = analyze(bundle([obj]))
        self.assertTrue(report["ok"])
        self.assertEqual(report["frame_groups"], [])
        self.assertEqual(report["pending"][0]["current_stage_name"], "iq_process")
        self.assertNotIn("lower_bound_ns", report["pending"][0])
        self.assertNotIn("duration", json.dumps(report["pending"]))

    def test_pending_snapshot_lower_bound_only_when_valid(self):
        for snapshot, flags, expected in (([10, 200], 0, 200), (None, 0, None),
                                          ([9, 0], 0, None), ([10, 200], 1, None)):
            with self.subTest(snapshot=snapshot, flags=flags):
                report = analyze(bundle([record(state="pending", end=None, flags=flags)],
                                        metadata={"snapshot_at": snapshot}))
                self.assertEqual(report["pending"][0].get("lower_bound_ns"), expected)
        report = analyze(bundle([record(state="pending", snapshot_at=[10, 99])]))
        self.assertEqual(report["pending"][0]["lower_bound_ns"], 99)

    def test_pending_snapshot_before_current_stage_is_not_a_valid_bound(self):
        report = analyze(bundle([record(state="pending", current_at=[10, 200], snapshot_at=[10, 100])]))
        self.assertNotIn("lower_bound_ns", report["pending"][0])

    def test_fixed_stage_names_and_missing_time_field(self):
        self.assertEqual(len(sf.STAGES), 29)
        self.assertEqual(len(set(sf.STAGES)), 29)
        obj = record()
        del obj["begin"]
        self.assert_bad(bundle([obj]), "missing begin")

    def test_boolean_timestamps_and_integer_schema_are_not_coerced(self):
        self.assert_bad(bundle([record(begin=[True, 0])]), "begin")
        self.assert_bad(bundle(metadata={"schema": True}), "schema")

    def test_control_epoch_flag_and_first_frame_contamination(self):
        report = analyze(bundle([record(0, epoch_end=4), record(1, flags=2),
                                 record(2, flags=128), record(3)]))
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)
        self.assertEqual(report["outcomes"]["excluded"], 3)
        self.assertIn("control_epoch_changed", report["excluded_records"][0]["reasons"])
        self.assertIn("first_after_control", report["excluded_records"][1]["reasons"])
        self.assertIn("control_epoch_changed", report["excluded_records"][2]["reasons"])

    def test_control_interval_overlap_and_touching_boundary(self):
        report = analyze(bundle([control(0, begin=[10, 50], end=[10, 80]),
                                 record(1), record(2, begin=[10, 80], end=[10, 200]),
                                 record(3, begin=[10, 81], end=[10, 200])]))
        self.assertEqual(report["outcomes"]["excluded"], 2)
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)
        self.assertTrue(all("control_overlap" in entry["reasons"] for entry in report["excluded_records"]))

    def test_incomplete_control_coverage_never_succeeds(self):
        variants = [bundle(metadata={"control_intervals_complete": False}),
                    bundle([control(0, complete=False), record(1)]),
                    bundle([control(0, end=None), record(1)]),
                    bundle([control(0, end=[8, 0]), record(1)])]
        for rows in variants:
            with self.subTest(rows=rows):
                report = analyze(rows)
                self.assertTrue(report["ok"])
                self.assertEqual(report["frame_groups"], [])
                self.assertIn("control_coverage_incomplete", report["excluded_records"][0]["reasons"])

    def test_control_interval_is_dump_local(self):
        rows = bundle([control(0, "a", begin=[10, 0], end=[11, 0])], "a")
        rows += bundle([record(0, "b")], "b")
        self.assertEqual(analyze(rows)["outcomes"]["frame_render_success"], 1)

    def test_busy_fallback_fault_and_nonsteady_separation(self):
        records = [record(0), record(1, path="passthrough", reason=6),
                   record(2, path="fallback", reason=8, fault=1),
                   record(3, stages={"effect": stage(80, rc=-3)}),
                   record(4, path="unknown", reason=0),
                   record(5, path="passthrough", reason=4),
                   record(6, path="passthrough", reason=5),
                   record(7, path="passthrough", reason=7, fault=1),
                   record(8, path="passthrough", reason=10)]
        report = analyze(bundle(records))
        for label in ("busy", "fallback_error", "stage_error", "excluded", "capture", "control", "fault", "no_context"):
            self.assertEqual(report["outcomes"][label], 1, report["outcomes"])
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)
        self.assertEqual(len(report["frame_groups"]), 1)
        self.assertEqual(report["fault_counts"]["1"], 2)

    def test_passthrough_and_successful_fallback_are_not_render(self):
        report = analyze(bundle([record(0), record(1, path="passthrough", reason=2),
                                 record(2, path="passthrough", reason=3),
                                 record(3, path="fallback", reason=8)]))
        self.assertEqual(len(report["frame_groups"]), 4)
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)
        self.assertEqual(report["outcomes"]["frame_passthrough_success"], 2)
        self.assertEqual(report["outcomes"]["frame_fallback_success"], 1)

    def test_reason_path_mismatch_refuses_trusted_statistics(self):
        self.assert_bad(bundle([record(reason=6)]), "reason/path mismatch")

    def test_original_contract_without_reason_still_supported(self):
        obj = record()
        del obj["reason"]
        report = analyze(bundle([obj]))
        self.assertTrue(report["ok"])
        self.assertEqual(report["outcomes"]["frame_render_success"], 1)

    def test_geometry_generation_and_process_grouping(self):
        rows = bundle([record(0), record(1, geometry=[320, 240, 640, 1]), record(2, generation=2)])
        rows += bundle([record(0, "start2")], "start2", metadata={"start": 4568})
        rows += bundle([record(0, "build2")], "build2", metadata={"build": BUILD + 1})
        rows += bundle([record(0, "pid2")], "pid2", metadata={"pid": 124})
        report = analyze(rows)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["frame_groups"]), 6)

    def test_camera_vm_unknown_not_merged_or_misrepresented(self):
        rows = []
        for source in ("camera", "vm", "unknown"):
            rows += bundle([record(0, source)], source, metadata={"measurement_source": source})
        report = analyze(rows)
        self.assertEqual(len(report["frame_groups"]), 3)
        self.assertEqual({g["key"]["measurement_source"] for g in report["frame_groups"]}, {"camera", "vm", "unknown"})

    def test_absent_vs_zero_calls_vs_real_zero_duration(self):
        report = analyze(bundle([record(0, stages={}),
                                 record(1, stages={"copy": stage(50, calls=0)}),
                                 record(2, stages={"copy": stage(0, calls=1)})]))
        group = report["frame_groups"][0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["metrics"]["copy"]["n"], 1)
        self.assertEqual(group["metrics"]["copy"]["max_ns"], 0)
        self.assertEqual(group["stage_coverage"]["copy"],
                         {"present": 2, "zero_calls": 1, "invalid_duration": 0, "calls": 1})
        self.assertNotIn("preview", group["metrics"])
        self.assertNotIn("preview", group["stage_coverage"])

    def test_invalid_stage_duration_never_becomes_sample(self):
        report = analyze(bundle([record(0, stages={"copy": {"sec": -1, "nsec": 1, "calls": 1, "rc": 0}}),
                                 record(1, stages={"copy": {"sec": 0, "nsec": sf.NS, "calls": 1, "rc": 0}})]))
        group = report["frame_groups"][0]
        self.assertNotIn("copy", group["metrics"])
        self.assertEqual(group["stage_coverage"]["copy"]["invalid_duration"], 2)

    def test_duration_sec_nsec_is_integer_exact(self):
        ns = 123456789 * sf.NS + 987654321
        report = analyze(bundle([record(stages={"iq_process": stage(ns, calls=3)})]))
        self.assertEqual(report["frame_groups"][0]["metrics"]["iq_process"]["p95_ns"], ns)
        self.assertEqual(report["frame_groups"][0]["stage_coverage"]["iq_process"]["calls"], 3)

    def test_cold_initialize_select_calls_not_zero(self):
        report = analyze(bundle([record(0, stages={"initialize": stage(10)}),
                                 record(1, stages={"select": stage(10)}),
                                 record(2, stages={"initialize": stage(999, calls=0)}), record(3)]))
        counts = {group["key"]["cohort"]: group["count"] for group in report["frame_groups"]}
        self.assertEqual(counts, {"cold": 2, "steady": 2})

    def test_prepare_finish_initialize_not_frames_or_disk_latency(self):
        records = [record(i, kind=kind, epoch_end=3, flags=2, path="unknown", reason=0,
                          stages={kind: stage(100)})
                   for i, kind in enumerate(("prepare", "finish", "initialize"))]
        report = analyze(bundle(records, metadata={"control_intervals_complete": False}))
        self.assertEqual(report["frame_groups"], [])
        self.assertEqual(len(report["lifecycle_groups"]), 3)
        self.assertEqual({group["key"]["kind"] for group in report["lifecycle_groups"]},
                         {"prepare", "finish", "initialize"})
        self.assertIn("not disk-write", " ".join(report["notes"]))

    def test_lifecycle_nonnegative_wrapper_returns_are_not_frame_rc(self):
        records = [record(0, kind="prepare", path="unknown", reason=0, rc=0,
                          stages={"prepare": stage(30, rc=0)}),
                   record(1, kind="finish", path="unknown", reason=0, rc=1,
                          stages={"finish": stage(40, rc=1)}),
                   record(2, kind="prepare", path="unknown", reason=0, rc=-2,
                          stages={"prepare": stage(30, rc=-2)}),
                   record(3, kind="finish", path="unknown", reason=0, rc=1,
                          stages={"finish": stage(40, rc=-1)}),
                   record(4, stages={"ycc": stage(50, rc=1)}),
                   record(5, rc=1)]
        report = analyze(bundle(records))
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["lifecycle_groups"]), 2)
        self.assertEqual(report["frame_groups"], [])
        self.assertEqual(report["outcomes"]["prepare_unknown_success"], 1)
        self.assertEqual(report["outcomes"]["finish_unknown_success"], 1)
        self.assertEqual(report["outcomes"]["stage_error"], 3)
        self.assertEqual(report["outcomes"]["record_error"], 1)
        self.assertEqual(report["record_metadata"][1]["rc"], 1)
        self.assertEqual(report["record_metadata"][1]["stage_rc"], {"finish": 1})
        self.assertEqual(report["record_metadata"][2]["rc"], -2)
        self.assertIn("not proof of disk completion", " ".join(report["notes"]))

    def test_string_look_ids_are_preserved_and_isolated(self):
        rows = []
        for look_id in ("stock", "identity", "monochrome"):
            rows += bundle([record(0, look_id)], look_id, metadata={"look_id": look_id})
        report = analyze(rows)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["frame_groups"]), 3)
        self.assertEqual({group["key"]["look_id"] for group in report["frame_groups"]},
                         {"stock", "identity", "monochrome"})
        for invalid in (None, "", True, -1, []):
            with self.subTest(invalid=invalid):
                self.assert_bad(bundle(metadata={"look_id": invalid}), "look_id")

    def test_real_s2_vm_three_export_regression_when_available(self):
        root = TOOL.parent.parent / "outputs/deployment/smartfilter-preview/s2-vm/guest/mfw-results"
        paths = sorted(root.glob("nx300m-sf-diag-*.ndjson"))
        if not paths:
            self.skipTest("optional S2 VM exports are not present; synthetic tests remain host-only")
        expected = {"stock": "passthrough", "identity": "render", "monochrome": "render"}
        required = set("ade_init lut0 lut1 lut2 flush_in ycc flush_out reset iq_init iq_process close".split())
        seen = set()
        for path in paths:
            with self.subTest(path=path.name):
                report = sf.analyze(sf.parse_log(path), entry_intervals=True)
                self.assertTrue(report["ok"], report["dumps"])
                metadata = report["dumps"][0]["metadata"]
                look_id = metadata["look_id"]
                seen.add(look_id)
                self.assertEqual(metadata["measurement_source"], "vm")
                if metadata["schema"] == 2 and metadata["probe"]["mode"] == 1:
                    # The new single attempt must never be required to supply
                    # the old continuous steady distribution.
                    self.assertEqual(len(report["probe_attempts"]), 1)
                    continue
                if look_id not in expected:
                    # New pending/capture/etc. exports must validate, but are
                    # not required to contain successful steady render frames.
                    continue
                steady = [group for group in report["frame_groups"] if group["key"]["cohort"] == "steady"]
                self.assertTrue(steady)
                self.assertTrue(all(group["key"]["path"] == expected[look_id] for group in steady))
                self.assertTrue(report["calibration_groups"])
                self.assertTrue(any("first_after_control" in entry["reasons"] for entry in report["excluded_records"]))
                if expected[look_id] == "render":
                    for group in steady:
                        self.assertTrue(required <= set(group["metrics"]), required - set(group["metrics"]))
                        self.assertTrue(all(group["stage_coverage"][name]["calls"] > 0 for name in required))
                self.assertIn("Frame totals", sf.to_markdown(report))
        self.assertTrue(set(expected) <= seen, set(expected) - seen)

    def test_calibration_separate_both_stage_and_record(self):
        report = analyze(bundle([record(0, stages={"effect": stage(80), "calibration": stage(7)}),
                                 record(1, kind="calibration", path="unknown", reason=0,
                                        stages={"calibration": stage(9)})]))
        self.assertEqual(len(report["frame_groups"]), 1)
        self.assertNotIn("calibration", report["frame_groups"][0]["metrics"])
        self.assertEqual(len(report["calibration_groups"]), 2)
        self.assertEqual({g["metrics"]["calibration"]["max_ns"] for g in report["calibration_groups"]}, {7, 9})
        self.assertEqual(report["lifecycle_groups"], [])

    def test_unknown_flags_and_masks_preserved_and_masked_samples_excluded(self):
        bit = 1 << sf.STAGES.index("effect")
        records = [record(0, flags=4, invalid_mask=bit, stage_mask=bit),
                   record(1, valid_mask=0)]
        report = analyze(bundle(records))
        self.assertEqual(report["outcomes"]["frame_render_success"], 2)
        self.assertEqual(report["record_metadata"][0]["unknown_flags"], 4)
        self.assertEqual(report["record_metadata"][0]["invalid_mask"], bit)
        self.assertEqual(report["record_metadata"][0]["stage_mask"], bit)
        self.assertEqual(report["record_metadata"][1]["valid_mask"], 0)
        self.assertNotIn("effect", report["frame_groups"][0]["metrics"])

    def test_dropped_and_clock_error_counts_reported(self):
        report = analyze(bundle(metadata={"dropped": 5, "clock_errors": 2}))
        self.assertEqual(report["dumps"][0]["metadata"]["dropped"], 5)
        self.assertEqual(len(report["dumps"][0]["warnings"]), 2)

    def test_entry_interval_optional_and_independent_sample_count(self):
        rows = bundle([entry_frame(0, 10), entry_frame(1, 30), entry_frame(2, 60)])
        default = analyze(rows)
        self.assertFalse(default["entry_intervals_enabled"])
        self.assertNotIn("entry_interval", default["frame_groups"][0]["metrics"])
        report = analyze(rows, entry_intervals=True)
        group = report["frame_groups"][0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["metrics"]["wall"]["n"], 3)
        self.assertEqual(group["metrics"]["entry_interval"],
                         {"n": 2, "p50_ns": 20, "p90_ns": 30, "p95_ns": 30, "max_ns": 30})
        self.assertEqual(group["key"]["tid"], 21)
        self.assertEqual(group["key"]["epoch"], 2)
        self.assertIn("record-entry interval", sf.to_markdown(report))
        self.assertNotIn("fps", json.dumps(group["metrics"]).lower())

    def test_entry_interval_first_after_control_not_an_endpoint(self):
        rows = bundle([entry_frame(0, 10, flags=2), entry_frame(1, 30), entry_frame(2, 60)])
        report = analyze(rows, entry_intervals=True)
        metric = report["frame_groups"][0]["metrics"]["entry_interval"]
        self.assertEqual(metric["n"], 1)
        self.assertEqual(metric["p50_ns"], 30)
        self.assertEqual(report["frame_groups"][0]["count"], 2)

    def test_entry_interval_pending_failed_and_polluted_break_chain(self):
        variants = [{"state": "pending"}, {"flags": 1}, {"flags": 2}, {"flags": 128},
                    {"fault": 1}, {"rc": -1}, {"stages": {"ycc": stage(1, rc=1)}},
                    {"path": "passthrough", "reason": 6}, {"epoch_end": 3},
                    {"kind": "prepare", "path": "unknown", "reason": 0}]
        for changes in variants:
            with self.subTest(changes=changes):
                rows = bundle([entry_frame(0, 10), entry_frame(1, 30, **changes),
                               entry_frame(2, 60), entry_frame(3, 100)])
                report = analyze(rows, entry_intervals=True)
                metrics = [g["metrics"]["entry_interval"] for g in report["frame_groups"]
                           if "entry_interval" in g["metrics"]]
                self.assertEqual(len(metrics), 1)
                self.assertEqual(metrics[0]["n"], 1)
                self.assertEqual(metrics[0]["max_ns"], 40)

    def test_entry_interval_config_and_epoch_changes_do_not_bridge(self):
        variants = [{"geometry": [320, 240, 640, 1]}, {"generation": 3},
                    {"path": "passthrough", "reason": 2},
                    {"epoch_begin": 3, "epoch_end": 3}, {"strength": 10}]
        for changes in variants:
            with self.subTest(changes=changes):
                report = analyze(bundle([entry_frame(0, 10), entry_frame(1, 30, **changes),
                                         entry_frame(2, 60), entry_frame(3, 100)]), entry_intervals=True)
                metrics = [g["metrics"]["entry_interval"] for g in report["frame_groups"]
                           if "entry_interval" in g["metrics"]]
                self.assertEqual(len(metrics), 1)
                self.assertEqual(metrics[0]["n"], 1)
                self.assertEqual(metrics[0]["max_ns"], 40)

    def test_entry_interval_tids_are_independent(self):
        report = analyze(bundle([entry_frame(0, 10, tid=21), entry_frame(1, 15, tid=22),
                                 entry_frame(2, 30, tid=21), entry_frame(3, 45, tid=22)]), entry_intervals=True)
        self.assertEqual(len(report["frame_groups"]), 2)
        self.assertEqual({g["key"]["tid"]: g["metrics"]["entry_interval"]["max_ns"]
                          for g in report["frame_groups"]}, {21: 20, 22: 30})

    def test_entry_interval_never_crosses_dump_or_window(self):
        rows = bundle([entry_frame(0, 10, dump_id="a")], "a", metadata={"window": 1})
        rows += bundle([entry_frame(0, 30, dump_id="b")], "b", metadata={"window": 1})
        rows += bundle([entry_frame(0, 50, dump_id="c")], "c", metadata={"window": 2})
        report = analyze(rows, entry_intervals=True)
        self.assertTrue(all("entry_interval" not in group["metrics"] for group in report["frame_groups"]))

    def test_entry_interval_nonincreasing_clock_is_not_sorted_into_success(self):
        for times, expected in (([100, 90, 110], 20), ([10, 10, 30], 20)):
            with self.subTest(times=times):
                rows = bundle([entry_frame(seq, at) for seq, at in enumerate(times)])
                rows = [rows[-1], rows[2], rows[0], rows[3], rows[1]]
                metric = analyze(rows, entry_intervals=True)["frame_groups"][0]["metrics"]["entry_interval"]
                self.assertEqual(metric["n"], 1)
                self.assertEqual(metric["max_ns"], expected)

    def test_entry_interval_control_between_endpoints_breaks_pair(self):
        report = analyze(bundle([entry_frame(0, 10), entry_frame(1, 40),
                                 control(2, begin=[10, 20], end=[10, 30]), entry_frame(3, 60)]),
                         entry_intervals=True)
        group = report["frame_groups"][0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["metrics"]["entry_interval"]["n"], 1)
        self.assertEqual(group["metrics"]["entry_interval"]["max_ns"], 20)

    def test_entry_interval_cold_steady_transition_is_not_a_fake_failure(self):
        report = analyze(bundle([entry_frame(0, 10, stages={"initialize": stage(1)}),
                                 entry_frame(1, 30)]), entry_intervals=True)
        steady = next(g for g in report["frame_groups"] if g["key"]["cohort"] == "steady")
        self.assertEqual(steady["metrics"]["entry_interval"]["n"], 1)
        self.assertEqual(steady["metrics"]["entry_interval"]["max_ns"], 20)

    def test_entry_interval_dropped_records_disable_unprovable_pairs(self):
        report = analyze(bundle([entry_frame(0, 10), entry_frame(1, 30)], metadata={"dropped": 1}),
                         entry_intervals=True)
        self.assertEqual(report["frame_groups"][0]["count"], 2)
        self.assertNotIn("entry_interval", report["frame_groups"][0]["metrics"])

    def test_entry_interval_cli_option(self):
        with tempfile.TemporaryDirectory(dir=TOOL.parent) as directory:
            source, output = Path(directory) / "entries.log", Path(directory) / "entries.json"
            source.write_bytes(b"".join(bundle([entry_frame(0, 10), entry_frame(1, 30)])))
            self.assertEqual(sf.main([str(source), "--json", str(output), "--entry-interval"]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["entry_intervals_enabled"])
            self.assertEqual(report["frame_groups"][0]["metrics"]["entry_interval"]["n"], 1)

    def test_no_residual_sum_or_fps_fabrication(self):
        report = analyze(bundle([record(stages={"preview": stage(100), "effect": stage(90),
                                                "ensure": stage(70), "copy": stage(60)})]))
        self.assertNotIn("residual", report["frame_groups"][0]["metrics"])
        self.assertIn("No residual", " ".join(report["notes"]))
        self.assertIn("No screen FPS", " ".join(report["notes"]))

    def test_empty_log_is_error(self):
        self.assert_bad([b"unrelated log\n"], "no smartfilter")

    def test_markdown_escapes_untrusted_identifiers(self):
        report = analyze(bundle(dump_id="d", metadata={"window": "a|b\nc`d"}))
        markdown = sf.to_markdown(report)
        self.assertIn(r"a\|b", markdown)
        self.assertNotIn("c`d", markdown)

    def test_cli_writes_reports_and_nonzero_on_bad_dump(self):
        with tempfile.TemporaryDirectory(dir=TOOL.parent) as directory:
            root = Path(directory)
            source, json_path, md_path = root / "input.log", root / "result.json", root / "result.md"
            source.write_bytes(b"".join(bundle()))
            command = [sys.executable, "-I", "-B", str(TOOL), str(source),
                       "--json", str(json_path), "--markdown", str(md_path), "--build", hex(BUILD)]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(json_path.read_text(encoding="utf-8"))["ok"])
            self.assertIn("Frame totals", md_path.read_text(encoding="utf-8"))
            source.write_bytes(b"".join(bundle()[:-1]))
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 1)
            self.assertIn("missing END", completed.stderr)
            self.assertFalse(json.loads(json_path.read_text(encoding="utf-8"))["ok"])

    def test_cli_missing_file_and_same_output_path(self):
        with tempfile.TemporaryDirectory(dir=TOOL.parent) as directory:
            source = Path(directory) / "input.log"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(sf.main([str(source)]), 2)
            source.write_bytes(b"".join(bundle()))
            original = source.read_bytes()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(sf.main([str(source), "--json", str(source)]), 2)
            self.assertEqual(source.read_bytes(), original)


class Schema2TimingTest(unittest.TestCase):
    assert_bad = TimingTest.assert_bad

    def test_single_probe_keeps_first_cold_select_without_quantiles(self):
        obj = record2(flags=256 | 2 | 4 | 8, stages={"preview": stage(100),
                      "effect": stage(90), "ensure": stage(70), "initialize": stage(60),
                      "select": stage(10), "ycc": stage(20)})
        report = analyze(bundle2([obj]), entry_intervals=True)
        self.assertTrue(report["ok"], report["dumps"])
        self.assertEqual(report["schema"], 2)
        self.assertEqual(report["frame_groups"], [])
        self.assertEqual(report["calibration_groups"], [])
        self.assertEqual(report["lifecycle_groups"], [])
        attempt = report["probe_attempts"][0]
        self.assertEqual(attempt["status"], "returned_success")
        detail = attempt["record"]
        self.assertEqual(detail["labels"], ["FIRST", "COLD", "SELECT"])
        self.assertEqual(detail["unknown_flags"], 0)
        self.assertEqual(detail["wall_ns"], 100)
        self.assertEqual(detail["stages"]["preview"]["duration_ns"], 100)
        self.assertEqual(detail["stages"]["effect"]["duration_ns"], 90)
        self.assertEqual(detail["stages"]["ensure"]["duration_ns"], 70)
        self.assertIn("first_after_control", detail["issues"])
        serialized = json.dumps(attempt)
        for forbidden in ("p95_ns", "p50_ns", "entry_interval", "residual", "sum_ns", "fps"):
            self.assertNotIn(forbidden, serialized)
        markdown = sf.to_markdown(report)
        self.assertIn("Probe attempts", markdown)
        self.assertIn("FIRST,COLD,SELECT", markdown)
        self.assertIn("| ensure | 1 | 0 | 70 |", markdown)

    def test_probe_and_budget_frames_are_not_steady_and_not_busy(self):
        records = [record2(0), record2(1, flags=0, path="passthrough", reason=11),
                   record2(2, flags=0, path="passthrough", reason=12),
                   record2(3, flags=0, path="passthrough", reason=6)]
        report = analyze(bundle2(records), entry_intervals=True)
        self.assertTrue(report["ok"], report["dumps"])
        self.assertEqual(report["frame_groups"], [])
        self.assertEqual(report["outcomes"]["probe_exhausted"], 1)
        self.assertEqual(report["outcomes"]["probe_blocked"], 1)
        self.assertEqual(report["outcomes"]["busy"], 1)
        self.assertEqual(len(report["probe_attempts"]), 1)
        for reason in (11, 12):
            self.assert_bad(bundle2([record2(reason=reason)]), "reason/path mismatch")
        self.assert_bad(bundle([record(path="passthrough", reason=11)]), "schema1")

    def test_probe_returned_nonsuccess_is_explicit_even_fallback_rc_zero(self):
        for outcome in range(2, 11):
            with self.subTest(outcome=outcome):
                report = analyze(bundle2([record2(path="fallback", reason=8)],
                                 metadata={"probe": probe_metadata(outcome=outcome)}))
                self.assertTrue(report["ok"])
                attempt = report["probe_attempts"][0]
                self.assertEqual(attempt["status"], "returned_nonsuccess")
                self.assertEqual(attempt["outcome_name"], sf.PROBE_OUTCOMES[outcome])
                self.assertEqual(attempt["record"]["rc"], 0)
                self.assertEqual(attempt["record"]["wall_ns"], 100)
                self.assertIn("returned_nonsuccess", attempt["issues"])
                self.assertEqual(report["frame_groups"], [])
                self.assertNotIn("frame_fallback_success", report["outcomes"])

    def test_declared_success_cannot_hide_record_stage_fault_or_restore_unlock(self):
        variants = [{"rc": -5}, {"stages": {"begin": stage(10, rc=-2)}},
                    {"stages": {"ycc": stage(10, rc=1)}},
                    {"stages": {"reset": stage(10, rc=-3)}},
                    {"stages": {"unlock": stage(10, rc=-4)}},
                    {"fault": 1}, {"flags": 256 | 32}, {"flags": 256 | 64},
                    {"path": "fallback", "reason": 8}]
        for changes in variants:
            with self.subTest(changes=changes):
                report = analyze(bundle2([record2(**changes)]))
                attempt = report["probe_attempts"][0]
                self.assertEqual(attempt["status"], "returned_error")
                self.assertEqual(report["frame_groups"], [])
                self.assertEqual(attempt["record"]["wall_ns"], 100)
                if changes.get("flags") == 288:
                    self.assertIn("restore_error", attempt["record"]["issues"])
                if changes.get("flags") == 320:
                    self.assertIn("unlock_error", attempt["record"]["issues"])

    def test_probe_control_pollution_does_not_erase_one_observation(self):
        records = [control(0, begin=[10, 30], end=[10, 40]),
                   record2(1, flags=256 | 128 | 2, epoch_end=3)]
        report = analyze(bundle2(records, metadata={"control_intervals_complete": False}))
        self.assertTrue(report["ok"], report["dumps"])
        detail = report["probe_attempts"][0]["record"]
        self.assertEqual(detail["wall_ns"], 100)
        self.assertTrue({"control_epoch_changed", "first_after_control", "control_overlap",
                         "control_coverage_incomplete"} <= set(detail["issues"]))
        self.assertEqual(report["frame_groups"], [])

    def test_pending_probe_has_no_completed_wall_or_stage_and_keeps_lower_bound(self):
        records = [record2(state="pending", end=None, current_stage=21, current_at=[10, 70],
                           stages={"ycc": stage(500)})]
        report = analyze(bundle2(records, metadata={"probe": probe_metadata(2),
                         "snapshot_at": [10, 200], "backend_active": 1, "inflight": 1}))
        self.assertTrue(report["ok"], report["dumps"])
        attempt = report["probe_attempts"][0]
        self.assertEqual(attempt["status"], "pending")
        self.assertEqual(attempt["runtime"], {"backend_active": 1, "inflight": 1})
        detail = attempt["record"]
        self.assertIsNone(detail["wall_ns"])
        self.assertIsNone(detail["stages"]["ycc"]["duration_ns"])
        self.assertEqual(detail["current_stage_name"], "iq_process")
        self.assertEqual(detail["lower_bound_ns"], 200)
        self.assertEqual(len(report["pending"]), 1)
        self.assertEqual(report["frame_groups"], [])
        self.assertIn("pending lower_bound ns: 200", sf.to_markdown(report))

    def test_blocked_and_armed_and_off_without_attempts(self):
        for state, mode, expected in ((4, 1, "blocked"), (1, 1, "not_attempted"), (0, 0, "off")):
            with self.subTest(state=state):
                rows = bundle2([], metadata={"probe": probe_metadata(state, mode=mode)})
                report = analyze(rows)
                self.assertTrue(report["ok"], report["dumps"])
                attempt = report["probe_attempts"][0]
                self.assertEqual(attempt["status"], expected)
                self.assertFalse(attempt["attempted"])
                self.assertFalse(attempt["attempt_record_present"])
                self.assertIsNone(attempt["record"])
                self.assertEqual(report["frame_groups"], [])
                self.assertIn(sf.PROBE_STATES[state], sf.to_markdown(report))

    def test_claimed_publication_intermediate_and_returned_missing_are_not_fake_success(self):
        for slot in (sf.UINT32_MAX, 7):
            with self.subTest(slot=slot):
                report = analyze(bundle2([], metadata={"probe": probe_metadata(2, slot=slot)}))
                self.assertTrue(report["ok"], report["dumps"])
                attempt = report["probe_attempts"][0]
                self.assertEqual(attempt["status"], "claimed_intermediate")
                self.assertTrue(attempt["attempted"])
                self.assertIn("attempt_record_unavailable", attempt["issues"])
        report = analyze(bundle2([], metadata={"probe": probe_metadata(), "dropped": 1}))
        self.assertTrue(report["ok"])
        self.assertEqual(report["probe_attempts"][0]["status"], "returned_record_unavailable")

    def test_committed_before_returned_publication_is_not_a_success(self):
        report = analyze(bundle2(metadata={"probe": probe_metadata(2)}))
        self.assertTrue(report["ok"])
        self.assertEqual(report["probe_attempts"][0]["status"], "committed_awaiting_return")
        self.assertEqual(report["frame_groups"], [])

    def test_create_initialize_associated_without_sum_or_group_statistics(self):
        records = [record2(0, kind="initialize", flags=4, path="unknown", reason=0,
                           begin=[9, 0], end=[9, 500], stages={"initialize": stage(500)}),
                   record2(1, flags=256 | 8, stages={"select": stage(20)})]
        report = analyze(bundle2(records, metadata={"probe": probe_metadata(slot=90)}))
        self.assertTrue(report["ok"], report["dumps"])
        self.assertEqual(report["lifecycle_groups"], [])
        self.assertEqual(report["frame_groups"], [])
        attempt = report["probe_attempts"][0]
        self.assertEqual(attempt["probe"]["slot"], 90)  # Not payload seq=1.
        self.assertEqual(attempt["record"]["wall_ns"], 100)
        self.assertEqual(attempt["initialize_records"][0]["wall_ns"], 500)
        self.assertEqual(attempt["initialize_records"][0]["seq"], 0)
        self.assertIn("Create initialize", sf.to_markdown(report))

    def test_initialize_other_generation_and_dump_not_falsely_associated(self):
        records = [record2(0, kind="initialize", flags=0, generation=2, reason=0, path="unknown"),
                   record2(1)]
        rows = bundle2(records)
        rows += bundle2([record2(0, "other", kind="initialize", flags=0, reason=0, path="unknown")],
                        "other", metadata={"probe": probe_metadata(1)})
        report = analyze(rows)
        self.assertTrue(report["ok"], report["dumps"])
        attempt = next(a for a in report["probe_attempts"] if a["dump_id"] == "d")
        self.assertEqual(attempt["initialize_records"], [])
        self.assertEqual(len(report["lifecycle_groups"]), 1)

    def test_failed_and_pending_initialize_remain_visible_independently(self):
        for state, rc in (("committed", -4), ("pending", 0)):
            with self.subTest(state=state):
                records = [record2(0, kind="initialize", flags=4, state=state, rc=rc,
                                   path="unknown", reason=0, stages={"initialize": stage(8, rc=rc)}),
                           record2(1)]
                report = analyze(bundle2(records))
                self.assertTrue(report["ok"])
                init = report["probe_attempts"][0]["initialize_records"][0]
                self.assertEqual(init["state"], state)
                self.assertEqual(init["rc"], rc)
                if state == "pending":
                    self.assertIsNone(init["wall_ns"])
                else:
                    self.assertIn("stage_error", init["issues"])
                self.assertEqual(report["lifecycle_groups"], [])

    def test_schema2_requires_probe_object_and_every_field(self):
        for value in (None, [], 0, True, "probe"):
            with self.subTest(value=value):
                self.assert_bad(bundle2(metadata={"probe": value}), "probe")
        for name in probe_metadata():
            with self.subTest(name=name):
                probe = probe_metadata()
                del probe[name]
                self.assert_bad(bundle2(metadata={"probe": probe}), "probe." + name)
        rows = bundle2()
        begin = json.loads(rows[0])
        del begin["probe"]
        rows[0] = line(begin)
        self.assert_bad(rows, "probe")

    def test_probe_numeric_boundaries_and_bool_are_exact(self):
        invalid = {"mode": (-1, 2, True, "1"), "state": (-1, 5, False, 2.0),
                   "outcome": (-1, 11, True, "SUCCESS"),
                   "slot": (-1, 128, 1024, 2**32, True, "0"),
                   "prepare_baseline": (-1, 2**32, True, "0"),
                   "finish_baseline": (-1, 2**32, False, 0.0),
                   "claimed": (0, 1, None, "true"), "returned": (0, 1, None, "false")}
        for name, values in invalid.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    self.assert_bad(bundle2(metadata={"probe": probe_metadata(**{name: value})}), "probe." + name)
        report = analyze(bundle2(metadata={"probe": probe_metadata(slot=127,
                         prepare_baseline=sf.UINT32_MAX, finish_baseline=sf.UINT32_MAX)}))
        self.assertTrue(report["ok"], report["dumps"])

    def test_state_claimed_returned_must_match_but_claimed_unbound_is_allowed(self):
        for state in range(5):
            for name in ("claimed", "returned"):
                with self.subTest(state=state, name=name):
                    probe = probe_metadata(state)
                    probe[name] = not probe[name]
                    self.assert_bad(bundle2([], metadata={"probe": probe}), "inconsistent with state")
        self.assertTrue(analyze(bundle2([], metadata={"probe": probe_metadata(2, slot=sf.UINT32_MAX)}))["ok"])

    def test_unique_probe_flag_kind_mode_and_schema_consistency(self):
        self.assert_bad(bundle2([record2(0), record2(1)]), "more than one flag256")
        self.assert_bad(bundle2([record2(kind="initialize")]), "only valid on a frame")
        self.assert_bad(bundle2(metadata={"probe": probe_metadata(0, mode=0)}), "requires mode=1")
        self.assert_bad(bundle2(ending={"schema": 1}), "schema mismatch")
        self.assert_bad(bundle(ending={"schema": 2}), "schema mismatch")
        self.assert_bad(bundle2([record2(schema=1)]), "schema mismatch")

    def test_schema2_integer_bounds_and_legacy_types_are_not_coerced(self):
        for name, value in (("window", "steady-1"), ("window", 2**32), ("generation", 2**32),
                            ("pid", 2**32), ("strength", -1), ("start", "123"),
                            ("start", 2**64), ("dropped", 2**32), ("backend_active", True)):
            with self.subTest(name=name):
                self.assert_bad(bundle2(metadata={name: value}), name)
        for name, value in (("flags", 2**32), ("tid", 2**32), ("rc", 2**31),
                            ("rc", -(2**31) - 1), ("generation", 2**32),
                            ("fault", 2**32), ("current_stage", 29), ("self", True)):
            with self.subTest(name=name, value=value):
                self.assert_bad(bundle2([record2(**{name: value})]), name)
        obj = record2()
        del obj["rc"]
        self.assert_bad(bundle2([obj]), "rc")
        del obj["reason"]
        self.assert_bad(bundle2([obj]), "reason")
        for name in ("sec", "nsec", "calls", "rc"):
            value = 2**31 if name == "rc" else 2**32
            self.assert_bad(bundle2([record2(stages={"copy": {**stage(1), name: value}})]), "stage." + name)

    def test_probe_stage_zero_missing_masks_and_clock_invalid_not_fabricated(self):
        bit = 1 << sf.STAGES.index("effect")
        obj = record2(invalid_mask=bit, stages={"effect": stage(80), "copy": stage(0),
                      "ycc": stage(100, calls=0)})
        detail = analyze(bundle2([obj]))["probe_attempts"][0]["record"]
        self.assertIsNone(detail["stages"]["effect"]["duration_ns"])
        self.assertEqual(detail["stages"]["copy"]["duration_ns"], 0)
        self.assertIsNone(detail["stages"]["ycc"]["duration_ns"])
        self.assertNotIn("preview", detail["stages"])
        for changes in ({"flags": 257}, {"begin": None}, {"end": [9, 0]}):
            with self.subTest(changes=changes):
                report = analyze(bundle2([record2(**changes)]))
                self.assertTrue(report["ok"])
                detail = report["probe_attempts"][0]["record"]
                self.assertIsNone(detail["wall_ns"])
                self.assertIn("invalid_clock", detail["issues"])

    def test_probe_breaks_entry_pairing_and_never_supplies_interval(self):
        records = [record2(0, flags=0, begin=[10, 10]),
                   record2(1, begin=[10, 30]),
                   record2(2, flags=0, begin=[10, 60]),
                   record2(3, flags=0, begin=[10, 80])]
        report = analyze(bundle2(records), entry_intervals=True)
        self.assertTrue(report["ok"], report["dumps"])
        group = report["frame_groups"][0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["metrics"]["entry_interval"]["n"], 1)
        self.assertEqual(group["metrics"]["entry_interval"]["max_ns"], 20)
        self.assertEqual(len(report["probe_attempts"]), 1)

    def test_schema2_cold_select_flags_cannot_become_steady_without_stages(self):
        rows = bundle2([record2(0, flags=4), record2(1, flags=8), record2(2, flags=0)],
                       metadata={"probe": probe_metadata(0, mode=0)})
        report = analyze(rows)
        self.assertTrue(report["ok"])
        self.assertEqual({g["key"]["cohort"]: g["count"] for g in report["frame_groups"]},
                         {"cold": 2, "steady": 1})

    def test_legacy_flag256_and_legacy_metadata_remain_supported(self):
        report = analyze(bundle([record(flags=256)], metadata={"start": "opaque", "window": "old"}))
        self.assertTrue(report["ok"])
        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["record_metadata"][0]["unknown_flags"], 256)
        self.assertEqual(len(report["frame_groups"]), 1)
        self.assertEqual(report["probe_attempts"], [])

    def test_interleaved_mixed_schemas_duplicates_hash_and_isolation(self):
        rows = bundle([record(0, "old")], "old") + bundle2([record2(0, "new")], "new")
        rows += [rows[4]]
        random.Random(27).shuffle(rows)
        report = analyze(rows)
        self.assertTrue(report["ok"], report["dumps"])
        self.assertEqual(len(report["frame_groups"]), 1)
        self.assertEqual(len(report["probe_attempts"]), 1)
        self.assertEqual(report["probe_attempts"][0]["dump_id"], "new")
        self.assert_bad(bundle2(ending={"sha256": "0" * 64}), "SHA256")
        damaged = bundle([record(0, "old")], "old") + bundle2([record2(0, "new")], "new")[:-1]
        report = analyze(damaged)
        self.assertFalse(report["ok"])
        self.assertEqual(report["probe_attempts"], [])
        self.assertEqual(len(report["frame_groups"]), 1)

    def test_schema2_cli_reports_and_errors(self):
        with tempfile.TemporaryDirectory(dir=TOOL.parent) as directory:
            root = Path(directory)
            source, output, markdown = root / "probe.log", root / "probe.json", root / "probe.md"
            source.write_bytes(b"".join(bundle2()))
            command = [sys.executable, "-I", "-B", str(TOOL), str(source), "--json", str(output),
                       "--markdown", str(markdown), "--entry-interval"]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["probe_attempts"][0]["status"], "returned_success")
            self.assertEqual(report["frame_groups"], [])
            self.assertIn("Probe attempts", markdown.read_text(encoding="utf-8"))
            source.write_bytes(b"".join(bundle2(metadata={"probe": None})))
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 1)
            self.assertIn("probe", completed.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["probe_attempts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
