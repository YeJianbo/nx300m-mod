"""Host faults against production persistence/export/probe headers, never ARM/VM.

Linux/WSL:
    python3 -I tools/test_smartfilter_persistence.py -v
Windows discovery imports safely; the POSIX/gcc class reports SkipTest.
No runtime/CLI/environment fault switch is added to the production loader.
"""
from __future__ import annotations

import errno
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = b'{"frozen":true,"host_test":1}\n'
FACTS = (
    "snapshot_created", "persisted", "write_attempted",
    "destination_may_have_changed", "destination_bytes_verified",
)
ERRORS = (
    "error_stage", "error_operation", "error_reason", "error_errno",
    "error_errno_valid", "error_errno_text",
)


def unique_json(line):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AssertionError(f"duplicate field: {key}")
            result[key] = value
        return result
    return json.loads(line, object_pairs_hook=pairs)


class DiscoveryContract(unittest.TestCase):
    def test_loader_only_inclusion_and_no_injection_switch(self):
        helper = (ROOT / "runtime/smartfilter_persistence.h").read_text(encoding="utf-8")
        timing = (ROOT / "runtime/smartfilter_timing_export.h").read_text(encoding="utf-8")
        loader = (ROOT / "runtime/smartfilter_loader.c").read_text(encoding="utf-8")
        self.assertIn('#include "smartfilter_persistence.h"', timing)
        self.assertNotIn("getenv(", helper)
        self.assertNotIn("NX_SFT_HOST", helper)
        self.assertIn("memset(&diag_save_result,0,sizeof diag_save_result)", loader)
        self.assertIn("int valid=diag_frozen_hash(frozen,0)", loader)
        # Identity/no-data gates still precede any pending export/save file IO.
        self.assertLess(loader.index("if(probe_expect.present&&"), loader.index('if(!strcmp(command,"probe-save"))'))
        self.assertIn('probe_failure(command,"no_data",0)', loader)


@unittest.skipUnless(sys.platform.startswith("linux"), "POSIX host fault injection requires Linux/WSL gcc")
class PersistenceHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which("gcc")
        if not gcc:
            raise unittest.SkipTest("gcc is not installed")
        cls.build_dir = tempfile.TemporaryDirectory(prefix="sf-persist-build-")
        cls.addClassCleanup(cls.build_dir.cleanup)
        build = Path(cls.build_dir.name)
        # Reuse the exact existing production SHA primitive, not a test copy.
        core = (ROOT / "runtime/live_loader_core.h").read_text(encoding="utf-8")
        sha = core[core.index("struct sha {"):core.index("static int file_hash(")]
        (build / "smartfilter_host_sha.inc").write_text(sha, encoding="utf-8")
        cls.binary = build / "persistence-host"
        command = [gcc, "-std=gnu99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                   "-Wno-unused-function", "-Wno-unused-variable", "-I", str(ROOT / "runtime"),
                   "-I", str(build), str(ROOT / "tools/smartfilter_persistence_host.c"), "-o", str(cls.binary)]
        compiled = subprocess.run(command, capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError("host gcc failed:\n" + compiled.stdout + compiled.stderr)

    def run_case(self, name):
        with tempfile.TemporaryDirectory(prefix="sf-persist-case-") as directory:
            run = subprocess.run([str(self.binary), name, directory], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, f"{name}: {run.stdout}\n{run.stderr}")
            lines = run.stdout.splitlines()
            self.assertEqual(len(lines), 2, f"{name}: {run.stdout}\n{run.stderr}")
            receipt, metrics = map(unique_json, lines)
            for key in FACTS:
                self.assertIs(type(receipt[key]), bool, (name, key))
            self.assertTrue(metrics.pop("host_metrics"))
            if "error_stage" in receipt:
                for key in ERRORS:
                    self.assertIn(key, receipt)
                for key in ERRORS[:3]:
                    self.assertRegex(receipt[key], r"^[a-z][a-z0-9_]*$")
                self.assertIs(type(receipt["error_errno"]), int)
                self.assertGreaterEqual(receipt["error_errno"], 0)
                self.assertIs(type(receipt["error_errno_valid"]), bool)
                if not receipt["error_errno_valid"]:
                    self.assertEqual(receipt["error_errno"], 0)
                    self.assertNotEqual(receipt["error_errno_text"], "Success")
                self.assertFalse(metrics["ok"])
                self.assertFalse(receipt["persisted"])
                self.assertEqual(len(run.stderr.splitlines()), 1, (name, run.stderr))
                for token in ("stage=", "op=", "reason=", "errno=", "valid=", "text="):
                    self.assertIn(token, run.stderr)
                self.assertLess(len(run.stderr), 1800)
            elif metrics["ok"]:
                self.assertFalse(run.stderr, (name, run.stderr))
            return receipt, metrics, run.stderr

    def test_fault_matrix(self):
        # name, stage, operation, reason, exact errno, append attempts, verified
        cases = [
            ("dst_write_enospc", "destination", "write", "syscall_failed", errno.ENOSPC, 1, False),
            ("dst_write_short", "destination", "write", "short_write", 0, 1, False),
            ("dst_write_zero", "destination", "write", "short_write", 0, 1, False),
            ("dst_fsync_eio", "destination", "fsync", "syscall_failed", errno.EIO, 1, False),
            ("dst_pread_eio", "destination_readback", "pread", "syscall_failed", errno.EIO, 1, False),
            ("dst_pread_short", "destination_readback", "pread", "short_read", 0, 1, False),
            ("dst_pread_mismatch", "destination_readback", "validate", "content_mismatch", 0, 1, False),
            ("dst_lseek_eio", "destination", "lseek", "syscall_failed", errno.EIO, 1, False),
            ("dst_lseek_short", "destination", "lseek", "offset_out_of_range", 0, 1, False),
            ("flock_busy", "destination", "flock", "syscall_failed", errno.EWOULDBLOCK, 0, False),
            ("actual_flock_busy", "destination", "flock", "syscall_failed", errno.EWOULDBLOCK, 0, False),
            ("bad_src_path", "source", "validate", "path_not_allowed", 0, 0, False),
            ("bad_dst_path", "destination", "validate", "path_not_allowed", 0, 0, False),
            ("parent_lstat_eacces", "destination_parent", "openat", "syscall_failed", errno.EACCES, 0, False),
            ("parent_type", "destination_parent", "openat", "syscall_failed", errno.ENOTDIR, 0, False),
            ("src_open_eacces", "source", "open", "syscall_failed", errno.EACCES, 0, False),
            ("src_stat_eio", "source", "fstat", "syscall_failed", errno.EIO, 0, False),
            ("src_type", "source", "validate", "not_regular", 0, 0, False),
            ("src_owner", "source", "validate", "owner_mismatch", 0, 0, False),
            ("src_mode", "source", "validate", "unsafe_permissions", 0, 0, False),
            ("src_oversize", "source", "validate", "size_out_of_range", 0, 0, False),
            ("src_empty", "source", "validate", "empty_source", 0, 0, False),
            ("src_read_eio", "source", "read", "syscall_failed", errno.EIO, 0, False),
            ("src_read_short", "source", "read", "short_read", 0, 0, False),
            ("source_malloc_enomem", "source", "malloc", "syscall_failed", errno.ENOMEM, 0, False),
            ("check_malloc_enomem", "destination_readback", "malloc", "syscall_failed", errno.ENOMEM, 1, False),
            ("dst_open_erofs", "destination", "openat", "syscall_failed", errno.EROFS, 0, False),
            ("dst_stat_eio", "destination", "fstat", "syscall_failed", errno.EIO, 0, False),
            ("dst_type", "destination", "validate", "not_regular", 0, 0, False),
            ("dst_owner", "destination", "validate", "owner_mismatch", 0, 0, False),
            ("dst_nonempty", "destination", "validate", "destination_not_empty", 0, 0, False),
            ("marker_lookup_eacces", "marker", "open", "syscall_failed", errno.EACCES, 0, False),
            ("marker_open_eacces", "marker", "open", "syscall_failed", errno.EACCES, 1, True),
            ("marker_write_enospc", "marker", "write", "syscall_failed", errno.ENOSPC, 1, True),
            ("marker_write_short", "marker", "write", "short_write", 0, 1, True),
            ("marker_fsync_eio", "marker", "fsync", "syscall_failed", errno.EIO, 1, True),
            ("marker_close_eio", "marker", "close", "syscall_failed", errno.EBADF, 1, True),
            ("dst_close_eio", "destination", "close", "syscall_failed", errno.EBADF, 1, True),
            ("old_marker_mismatch", "marker", "validate", "marker_mismatch", 0, 0, False),
            ("old_marker_read_eio", "marker", "read", "syscall_failed", errno.EIO, 0, False),
            ("old_marker_read_short", "marker", "read", "short_read", 0, 0, False),
            ("old_marker_stat_eio", "marker", "fstat", "syscall_failed", errno.EIO, 0, False),
            ("old_marker_type", "marker", "validate", "not_regular", 0, 0, False),
            ("old_marker_owner", "marker", "validate", "owner_mismatch", 0, 0, False),
            ("old_marker_mode", "marker", "validate", "unsafe_permissions", 0, 0, False),
            ("cleanup_preserve", "destination", "write", "syscall_failed", errno.ENOSPC, 1, False),
            ("marker_cleanup_preserve", "marker", "write", "syscall_failed", errno.ENOSPC, 1, True),
        ]
        for name, stage, op, reason, number, attempts, verified in cases:
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual((receipt["error_stage"], receipt["error_operation"], receipt["error_reason"], receipt["error_errno"]),
                                 (stage, op, reason, number))
                self.assertEqual(receipt["error_errno_valid"], bool(number))
                self.assertEqual(metrics["appends"], attempts)
                self.assertEqual(receipt["write_attempted"], bool(attempts))
                self.assertEqual(receipt["destination_may_have_changed"], bool(attempts))
                self.assertEqual(receipt["destination_bytes_verified"], verified)
                if reason in ("short_write", "short_read"):
                    expected = metrics["marker_layout_size"] if stage == "marker" else len(PAYLOAD)
                    self.assertEqual(receipt["error_expected_bytes"], expected)
                    actual = 0 if name == "dst_write_zero" else expected - 1
                    self.assertEqual(receipt["error_actual_bytes"], actual)
                if not attempts:
                    self.assertEqual(metrics["destination_size"], len(PAYLOAD) if name.startswith("old_marker_") else 3 if name == "dst_nonempty" else 0)
                if name == "dst_write_short":
                    self.assertEqual(metrics["destination_size"], len(PAYLOAD) - 1)
                    self.assertEqual(metrics["marker_size"], -1)
                if name == "marker_write_short":
                    self.assertEqual(metrics["marker_size"], metrics["marker_layout_size"] - 1)

    def test_success_and_idempotency(self):
        for name in ("success", "ordinary_success", "ordinary_create", "idempotent", "idempotent_close_eio", "src_close_eio"):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertTrue(metrics["ok"])
                self.assertTrue(receipt["persisted"])
                self.assertNotIn("error_stage", receipt)
                self.assertTrue(receipt["destination_bytes_verified"])
                self.assertEqual(metrics["destination_size"], len(PAYLOAD))
                self.assertEqual(metrics["marker_size"], metrics["marker_layout_size"])
                repeated = name.startswith("idempotent")
                self.assertEqual(metrics["appends"], 0 if repeated else 1)
                self.assertEqual(metrics["marker_writes"], 0 if repeated else 1)
                self.assertEqual(receipt["write_attempted"], not repeated)

    def test_checksum_failures(self):
        for name, op, reason, number in (
            ("side_open_eacces", "open", "syscall_failed", errno.EACCES),
            ("side_write_enospc", "write", "syscall_failed", errno.ENOSPC),
            ("side_write_short", "write", "short_write", 0),
            ("side_fsync_eio", "fsync", "syscall_failed", errno.EIO),
            ("side_close_eio", "close", "syscall_failed", errno.EBADF),
            ("side_read_open_eacces", "open", "syscall_failed", errno.EACCES),
            ("side_read_eio", "read", "syscall_failed", errno.EIO),
            ("side_read_short", "read", "short_read", 0),
            ("side_invalid_hash", "validate", "invalid_hash", 0),
            ("checksum_lseek_eio", "lseek", "syscall_failed", errno.EIO),
            ("checksum_mismatch", "validate", "content_mismatch", 0),
        ):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual((receipt["error_stage"], receipt["error_operation"], receipt["error_reason"], receipt["error_errno"]),
                                 ("checksum", op, reason, number))
                self.assertEqual(metrics["appends"], 0)
                self.assertFalse(receipt["write_attempted"])
                if reason in ("short_write", "short_read"):
                    self.assertEqual((receipt["error_expected_bytes"], receipt["error_actual_bytes"]), (65, 64))

    def test_snapshot_export_real_code(self):
        for name, op, number in (
            ("snapshot_open_eacces", "open", errno.EACCES),
            ("snapshot_fdopen_emfile", "fdopen", errno.EMFILE),
            ("snapshot_fwrite_short", "fwrite", 0),
            ("snapshot_fflush_enospc", "fflush", errno.ENOSPC),
            ("snapshot_fsync_eio", "fsync", errno.EIO),
            ("snapshot_fclose_eio", "fclose", errno.EIO),
            ("snapshot_rename_eacces", "rename", errno.EACCES),
        ):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual(receipt["command"], "diag-export")
                self.assertEqual(receipt["status"], "export_failed")
                self.assertFalse(receipt["snapshot_created"])
                self.assertEqual((receipt["error_stage"], receipt["error_operation"], receipt["error_errno"]), ("snapshot", op, number))
                self.assertEqual(metrics["source_size"], -1)
                self.assertEqual(metrics["appends"], 0)
        receipt, metrics, _ = self.run_case("snapshot_success")
        self.assertEqual(receipt["status"], "ram_only")
        self.assertTrue(receipt["snapshot_created"])
        self.assertFalse(receipt["persisted"])
        self.assertEqual(metrics["side_size"], 65)
        self.assertGreater(metrics["source_size"], len(PAYLOAD))

    def test_snapshot_signing_failure_is_not_a_valid_snapshot(self):
        for name, op, number, valid_snapshot in (
            ("snapshot_side_open_eacces", "open", errno.EACCES, False),
            ("snapshot_side_write_enospc", "write", errno.ENOSPC, False),
            ("snapshot_side_write_short", "write", 0, False),
            ("snapshot_side_fsync_eio", "fsync", errno.EIO, True),
            ("snapshot_side_close_eio", "close", errno.EBADF, True),
        ):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual(receipt["status"], "export_failed")
                self.assertEqual(receipt["snapshot_created"], valid_snapshot)
                self.assertFalse(receipt["persisted"])
                self.assertEqual((receipt["error_stage"], receipt["error_operation"], receipt["error_errno"]), ("checksum", op, number))
                self.assertGreater(metrics["source_size"], 0, "a bare snapshot file exists but is not automatically valid")
                self.assertEqual(metrics["appends"], 0)

    def test_real_receipts_and_probe_gates(self):
        for name, command in (("receipt_write_enospc", "diag-export"), ("probe_write_enospc", "probe-save"), ("probe_marker_enospc", "probe-save")):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual(receipt["command"], command)
                self.assertTrue(receipt["snapshot_created"])
                self.assertFalse(receipt["persisted"])
                self.assertEqual(receipt["error_errno"], errno.ENOSPC)
                self.assertEqual(receipt["destination_bytes_verified"], name == "probe_marker_enospc")
                self.assertEqual(metrics["appends"], 1)
        for name, status in (("probe_contract", "probe_contract_missing"), ("probe_identity", "probe_contract_missing"),
                             ("probe_media_identity", "media_identity_changed"), ("probe_capture", "capture_observed"),
                             ("quiet_no_data", "no_data")):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual(receipt["status"], status)
                if name == "probe_media_identity":
                    self.assertEqual((receipt["error_stage"], receipt["error_reason"]),
                                     ("media_identity", "source_identity_mismatch"))
                else:
                    self.assertNotIn("error_stage", receipt)
                self.assertFalse(receipt["persisted"])
                self.assertFalse(receipt["write_attempted"])
                self.assertEqual(metrics["appends"], 0)
                if name in ("probe_contract", "probe_identity"):
                    self.assertEqual(metrics["calls"], 0, "identity rejection must not open files")
                    self.assertFalse(receipt["snapshot_created"])
        receipt, metrics, _ = self.run_case("probe_media_stat_eacces")
        self.assertEqual(receipt["status"], "media_identity_changed")
        self.assertEqual((receipt["error_stage"], receipt["error_operation"], receipt["error_errno"]), ("media_identity", "fstat", errno.EACCES))
        self.assertTrue(receipt["snapshot_created"])
        self.assertEqual(metrics["appends"], 0)
        for name in ("probe_no_snapshot", "probe_checksum_mismatch"):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual(receipt["status"], "no_valid_snapshot")
                self.assertFalse(receipt["snapshot_created"])
                self.assertEqual(metrics["appends"], 0)
        receipt, metrics, _ = self.run_case("probe_success")
        self.assertEqual(receipt["status"], "persisted")
        self.assertTrue(receipt["snapshot_created"])
        self.assertTrue(receipt["persisted"])
        self.assertEqual(metrics["destination_size"], len(PAYLOAD))
        for name, command in (("probe_idempotent", "probe-save"), ("receipt_idempotent", "diag-export")):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual(receipt["command"], command)
                self.assertEqual(receipt["status"], "persisted")
                self.assertTrue(receipt["persisted"])
                self.assertTrue(receipt["destination_bytes_verified"])
                self.assertFalse(receipt["write_attempted"])
                self.assertEqual(metrics["destination_size"], len(PAYLOAD))
                self.assertEqual(metrics["appends"], 0)
                self.assertEqual(metrics["marker_writes"], 0)

    def test_media_fd_faults_fail_closed_before_append(self):
        # Synthetic FAT/exFAT cases are rejection-only. Real FAT images are a
        # separate native integration gate, never inferred from these faults.
        cases = (
            ("parent_openat_eacces", "destination_parent", "openat", "syscall_failed", errno.EACCES),
            ("media_root_fstatfs_eio", "media_identity", "fstatfs", "syscall_failed", errno.EIO),
            ("probe_media_fs_eio", "media_identity", "fstatfs", "syscall_failed", errno.EIO),
            ("media_target_fstatfs_eio", "destination", "fstatfs", "syscall_failed", errno.EIO),
            ("media_target_device", "destination", "validate", "device_mismatch", 0),
            ("media_target_fs_type", "destination", "validate", "filesystem_mismatch", 0),
            ("media_target_fsid", "destination", "validate", "filesystem_mismatch", 0),
            ("media_fixture_fat_owner", "destination", "validate", "owner_mismatch", 0),
            ("media_fixture_exfat_owner", "destination", "validate", "owner_mismatch", 0),
            ("media_fixture_unknown_owner", "destination", "validate", "owner_mismatch", 0),
            ("media_prod_anchor_fs_eio", "media_anchor", "fstatfs", "syscall_failed", errno.EIO),
            ("media_prod_root_device", "media_identity", "validate", "device_mismatch", 0),
            ("media_prod_root_fs_type", "media_identity", "validate", "filesystem_mismatch", 0),
            ("media_prod_logs_device", "destination_parent", "validate", "device_mismatch", 0),
            ("media_prod_logs_fs_eio", "destination_parent", "fstatfs", "syscall_failed", errno.EIO),
            ("media_prod_target_owner", "destination", "validate", "owner_mismatch", 0),
            ("probe_media_inode_drift", "media_identity", "validate", "source_identity_mismatch", 0),
            ("probe_media_real_replaced", "media_identity", "validate", "source_identity_mismatch", 0),
        )
        for name, stage, op, reason, number in cases:
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual((receipt["error_stage"], receipt["error_operation"], receipt["error_reason"], receipt["error_errno"]),
                                 (stage, op, reason, number))
                self.assertEqual(receipt["error_errno_valid"], bool(number))
                self.assertFalse(receipt["write_attempted"])
                self.assertFalse(receipt["destination_bytes_verified"])
                self.assertEqual(metrics["appends"], 0)
                self.assertEqual(metrics["marker_writes"], 0)
                self.assertEqual(receipt["media_owner_policy"], "posix_owner")
                self.assertEqual(receipt["media_production_path"], name.startswith("media_prod_"))
        for name, expected_fs in (("media_fixture_fat_owner", 0x4d44),
                                  ("media_fixture_exfat_owner", 0x2011bab0),
                                  ("media_fixture_unknown_owner", 0x12345678)):
            receipt, _, _ = self.run_case(name)
            self.assertEqual(receipt["media_target_fs_type"], expected_fs)
            self.assertEqual(receipt["media_target_uid"], receipt["media_euid"] + 1)
            self.assertIsNone(receipt["media_anchor_uid"])
        receipt, _, _ = self.run_case("media_target_fstatfs_eio")
        self.assertIsInstance(receipt["media_target_uid"], int)
        self.assertIsNone(receipt["media_target_fs_type"])
        receipt, _, _ = self.run_case("media_root_fstatfs_eio")
        self.assertIsInstance(receipt["media_root_uid"], int)
        self.assertIsNone(receipt["media_root_fs_type"])
        self.assertIsNone(receipt["media_target_uid"])

    def test_real_nofollow_and_held_root_identity(self):
        for name, stage in (("parent_actual_symlink", "destination_parent"),
                            ("target_actual_symlink", "destination"),
                            ("media_prod_logs_symlink", "destination_parent")):
            with self.subTest(case=name):
                receipt, metrics, _ = self.run_case(name)
                self.assertEqual((receipt["error_stage"], receipt["error_operation"]), (stage, "openat"))
                self.assertTrue(receipt["error_errno_valid"])
                self.assertIn(receipt["error_errno"], (errno.ENOTDIR, errno.ELOOP))
                self.assertEqual(metrics["appends"], 0)
                self.assertEqual(metrics["marker_writes"], 0)
        # Rename the source after its FD is open, then plant a different same-
        # name directory/target. The expected source and target stay on the held
        # original root, and the replacement target remains foreign bytes.
        receipt, metrics, _ = self.run_case("probe_media_root_replaced")
        self.assertTrue(receipt["persisted"])
        self.assertEqual(metrics["destination_size"], len(PAYLOAD))
        self.assertEqual(metrics["appends"], 1)

    def test_posix_policy_and_ram_policy_are_not_globally_relaxed(self):
        receipt, metrics, _ = self.run_case("media_prod_success")
        self.assertTrue(metrics["ok"])
        self.assertEqual(receipt["media_owner_policy"], "posix_owner")
        self.assertTrue(receipt["media_production_path"])
        for part in ("anchor", "root", "logs", "target"):
            self.assertIsInstance(receipt[f"media_{part}_uid"], int)
            self.assertIsInstance(receipt[f"media_{part}_fs_type"], int)
        # These wrap the actual RAM validator, not the media admission helper.
        for name in ("src_owner", "src_mode", "old_marker_owner", "old_marker_mode"):
            receipt, metrics, _ = self.run_case(name)
            self.assertIn(receipt["error_reason"], ("owner_mismatch", "unsafe_permissions"))
            self.assertEqual(metrics["appends"], 0)

    def test_bounded_json_and_stderr_escaping(self):
        receipt, _, stderr = self.run_case("quote_escape")
        self.assertEqual(receipt["error_errno_text"], 'quoted" slash\\ line\n\t\x01ÿ')
        self.assertNotIn("\x01", stderr)
        self.assertIn('quoted\\" slash\\\\ line\\u000a\\u0009\\u0001\\u00ff', stderr)


if __name__ == "__main__":
    unittest.main()
