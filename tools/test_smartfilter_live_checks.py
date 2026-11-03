"""Offline continuous-package routing checks; physical card access is forbidden."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import deploy_smartfilter_card as deploy
import package_smartfilter_live as live


@contextmanager
def candidate(*, timing=False):
    with tempfile.TemporaryDirectory(prefix='sf-live-offline-') as directory, ExitStack() as stack:
        root = Path(directory)
        payloads = {'nx300m-mod/gui_preview.NX300M': b'continuous test menu\n'}
        report = dict(profile='S2', abi=4, continuous=True, probe=False, diagnostic=False,
                      build=1, camera_button_preview_menu_vm_verified=True,
                      files=[dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                             for name, data in payloads.items()])
        suffix = '-timing' if timing else ''
        if timing:
            report.update(diagnostic=True, opt_in_timing=True, default_timing=False,
                          diagnostic_runtime_schema=2)
        (root / f'live{suffix}-package-report.json').write_text(json.dumps(report))
        for gate in ('arm-vm', 'ram-vm', 's2-vm'):
            (root / gate).mkdir()
            (root / gate / 'report.json').write_text(json.dumps(dict(passed=True, private_vm_stopped=True)))
        with zipfile.ZipFile(root / f'nx300m-smartfilter-s2-live{suffix}.zip', 'w') as archive:
            for name, data in payloads.items():
                archive.writestr(name, data)
        stack.enter_context(patch.object(deploy, 'BASE', root))
        stack.enter_context(patch.object(sys, 'argv', ['deploy', '--s2', '--live-timing' if timing else '--live', '--check-only']))
        validator = stack.enter_context(patch.object(live, 'validate_candidate', return_value=({}, {}, None, payloads)))
        stack.enter_context(patch.object(deploy.card, 'volume', side_effect=AssertionError('card access forbidden')))
        stack.enter_context(patch.object(deploy.card, 'deploy', side_effect=AssertionError('card write forbidden')))
        stack.enter_context(redirect_stdout(io.StringIO()))
        yield root, report, payloads, validator


class ContinuousDeployment(unittest.TestCase):
    def test_live_timing_selects_its_own_package_without_card_access(self):
        with candidate(timing=True) as (_, _, _, validator):
            deploy.main()
            validator.assert_called_once_with()

    def test_live_timing_rejects_default_timing_enabled(self):
        with candidate(timing=True) as (root, report, _, _):
            report['default_timing'] = True
            (root / 'live-timing-package-report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(RuntimeError, 'default timing disabled'):
                deploy.main()

    def test_live_check_only_has_no_card_access(self):
        with candidate() as (_, _, _, validator):
            deploy.main()
            validator.assert_called_once_with()

    def test_live_requires_s2_before_reading_reports(self):
        with patch.object(sys, 'argv', ['deploy', '--live', '--check-only']), redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                deploy.main()

    def test_live_and_probe_are_mutually_exclusive(self):
        with patch.object(sys, 'argv', ['deploy', '--s2', '--live', '--probe', '--check-only']), patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                deploy.main()

    def test_package_flags_are_checked(self):
        for field, value in (('continuous', False), ('abi', 3), ('probe', True), ('diagnostic', True)):
            with self.subTest(field=field), candidate() as (root, report, _, _):
                report[field] = value
                (root / 'live-package-report.json').write_text(json.dumps(report))
                with self.assertRaises(RuntimeError):
                    deploy.main()

    def test_archive_bytes_must_equal_admitted_candidate(self):
        with candidate() as (root, _, _, _):
            with zipfile.ZipFile(root / 'nx300m-smartfilter-s2-live.zip', 'w') as archive:
                archive.writestr('nx300m-mod/gui_preview.NX300M', b'changed\n')
            with self.assertRaisesRegex(RuntimeError, 'admitted bytes'):
                deploy.main()

    def test_missing_inventory_is_rejected(self):
        with candidate() as (root, report, _, _):
            report['files'] = []
            (root / 'live-package-report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(RuntimeError, 'inventory'):
                deploy.main()

    def test_gate_failure_never_reaches_card(self):
        with candidate() as (_, _, _, validator):
            validator.side_effect = ValueError('live VM failed')
            with self.assertRaisesRegex(ValueError, 'live VM failed'):
                deploy.main()

    def test_start_guide_and_ram_assets_are_separate(self):
        self.assertEqual(deploy.start_guide(live=True), 'SMARTFILTER-LIVE-START.md')
        self.assertEqual(deploy.start_guide(live_timing=True), 'SMARTFILTER-LIVE-TIMING-START.md')
        self.assertNotEqual(deploy.start_guide(live=True), deploy.start_guide(probe=True))
        required = deploy.runtime_manifest_entries(live=True)
        for name in ('smartfilter-preview.sh', 'smartfilter-probe-collector.sh',
                     'bin/smartfilter_probe_launcher.arm', 'preview-tests/monochrome.d4lut'):
            self.assertIn(name, required)

    def test_probe_policy_is_restored_even_on_failure(self):
        previous = (live.probe.FILES, live.probe.CONTROL_FILES, live.probe.REQUIRED_CONTROL_CHECKS)
        with self.assertRaisesRegex(ValueError, 'test policy failure'):
            with live.continuous_policy():
                self.assertIs(live.probe.FILES, live.FILES)
                raise ValueError('test policy failure')
        self.assertEqual(previous, (live.probe.FILES, live.probe.CONTROL_FILES, live.probe.REQUIRED_CONTROL_CHECKS))
        self.assertIn('continuous_actions_rejected', live.probe.REQUIRED_CONTROL_CHECKS)


if __name__ == '__main__':
    unittest.main()
