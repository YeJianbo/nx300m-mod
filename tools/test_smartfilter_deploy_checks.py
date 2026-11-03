"""Offline deployment admission tests. No volume or camera is accessed."""
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

# Explicitly trust project tools even under python -I; fixture roots below are
# never added to the import path and never hold executable test code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_smartfilter_card as deploy

probe_package = deploy.probe_package


class OfflineDeployment(unittest.TestCase):
    def run_candidate(self, *, explicit=True, valid_hash=True, valid_vm=True, name='README.md',
                      timing=False, diagnostic=None, abi=3):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            data=b'offline candidate only\n'
            report=dict(profile='S2',camera_button_preview_menu_vm_verified=True,files=[dict(
                path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest() if valid_hash else '0'*64)])
            report.update(diagnostic=timing if diagnostic is None else diagnostic,abi=abi)
            report_name='timing-package-report.json' if timing else 'package-report.json'
            (root/report_name).write_text(json.dumps(report))
            for probe in ('arm-vm','ram-vm','s2-vm'):
                (root/probe).mkdir()
                (root/probe/'report.json').write_text(json.dumps(dict(passed=valid_vm,private_vm_stopped=True)))
            archive='nx300m-smartfilter-s2-timing.zip' if timing else 'nx300m-smartfilter-s2-preview.zip'
            with zipfile.ZipFile(root/archive,'w') as package:
                package.writestr(name,data)
            arguments=['deploy_smartfilter_card.py','--check-only']+(['--s2'] if explicit else [])
            if timing:
                arguments.append('--timing')
            output=io.StringIO()
            with patch.object(deploy,'BASE',root),patch.object(sys,'argv',arguments),\
                 patch.object(deploy.card,'volume',side_effect=AssertionError('card access forbidden')),\
                 patch.object(deploy.card,'deploy',side_effect=AssertionError('card write forbidden')),\
                 redirect_stdout(output):
                deploy.main()
            return output.getvalue()

    def test_check_only_never_touches_volume_or_deployer(self):
        self.assertIn('no card accessed or written',self.run_candidate())

    def test_s2_requires_explicit_selection(self):
        with self.assertRaisesRegex(RuntimeError,'explicit --s2'):
            self.run_candidate(explicit=False)

    def test_changed_bytes_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'Candidate bytes differ'):
            self.run_candidate(valid_hash=False)

    def test_unfinished_vm_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'verification has not passed'):
            self.run_candidate(valid_vm=False)

    def test_archive_traversal_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'Unexpected candidate path'):
            self.run_candidate(name='nx300m-mod/../outside')

    def test_timing_check_only_selects_separate_archive_without_card_access(self):
        output=self.run_candidate(timing=True)
        self.assertIn('nx300m-smartfilter-s2-timing.zip',output)
        self.assertIn('no card accessed or written',output)

    def test_timing_requires_explicit_s2_before_reading_report(self):
        with redirect_stdout(io.StringIO()),patch('sys.stderr',new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as raised:
                self.run_candidate(timing=True,explicit=False)
        self.assertEqual(raised.exception.code,2)

    def test_timing_rejects_non_diagnostic_report(self):
        with self.assertRaisesRegex(RuntimeError,'ABI3 timing diagnostic'):
            self.run_candidate(timing=True,diagnostic=False)

    def test_timing_rejects_old_abi(self):
        with self.assertRaisesRegex(RuntimeError,'ABI3 timing diagnostic'):
            self.run_candidate(timing=True,abi=2)

    def test_diagnostic_report_requires_explicit_timing(self):
        with self.assertRaisesRegex(RuntimeError,'explicit --timing'):
            self.run_candidate(diagnostic=True)


@contextmanager
def probe_fixture():
    """Synthetic admission evidence ONLY, confined to a disposable temp root.

    This is not a VM run/report and never writes project outputs or a card.
    Packager main() is deliberately not invoked by any test.
    """
    with tempfile.TemporaryDirectory(prefix='sf-probe-offline-') as folder:
        root = Path(folder)
        data = {name: ('synthetic offline fixture: ' + name + '\n').encode()
                for name in probe_package.FILES}
        header = [0] * 18
        header[0], header[1], header[2] = 0x5246534e, 4, 96
        header[14], header[16], header[17] = 4, 16, 0x30000004
        data['nx300m-mod/lib/smartfilter_ram.bin'] = struct.pack('<18I', *header) + bytes(24)
        files = {}
        for name, payload in data.items():
            source = root / 'inputs' / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(payload)
            files[name] = source
        build = dict(build_exit_code=0, abi=4, build=header[17], hook_count=7,
                     s1_hook_count=5, s2_hook_count=7, hooks=list(range(7)),
                     blob_bytes=96, config_bytes=4, runtime_bytes=16)
        source_root = root / 'media-sources'
        source_hashes = {}
        for name in probe_package.MEDIA_SOURCES:
            path = source_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = ('synthetic offline source only: ' + name).encode()
            path.write_bytes(payload)
            source_hashes[name] = hashlib.sha256(payload).hexdigest()
        candidate_hashes = {name: hashlib.sha256(data[target]).hexdigest()
                            for name, target in probe_package.MEDIA_CANDIDATES.items()}
        build['control_sources'] = {name: source_hashes[name]
                                    for name in probe_package.MEDIA_REQUIRED_CONTROL_SOURCES}
        build['control_artifacts'] = {name: candidate_hashes[name] for name in
                                     ('smartfilter_loader.arm', 'smartfilter_probe_launcher.arm')}
        # Explicit synthetic admission data: never a claim of executing a FAT test.
        media = dict(passed=True, namespace_exit=0, source_and_candidate_bytes_stable=True,
            physical_card_accessed=False, sources=source_hashes, candidates=candidate_hashes,
            filesystem_run=dict(passed=True, private_mount_namespace=True,
                native_production_code=True, physical_card_accessed=False,
                image_created_exclusively=True, all_mounts_removed=True, loop_released=True,
                cleanup_errors=[], checks={'synthetic_offline_admission_only': True},
                fat32=dict(tested=True, passed=True, native_only=True, mocked_fs_or_uid=False,
                    observations=[dict(umask=mask, uid=65534, euid=0, fs_type=0x4d44)
                                  for mask in ('0022', '0000')])) )
        (root / 'media-fs').mkdir()
        (root / 'media-fs/report.json').write_text(json.dumps(media), encoding='utf-8')
        (root / 'ram-build-report.json').write_text(json.dumps(build), encoding='utf-8')
        for name in probe_package.VM_GATES:
            vm = root / name
            vm.mkdir()
            report = dict(passed=True, private_vm_stopped=True)
            if name == 'arm-vm':
                report['checks'] = [dict(check='synthetic_boundary_fixture', passed=True)]
            elif name == 'ram-vm':
                report.update(actual_ram_loader=True, actual_plt_hooks=5)
            elif name == 's2-vm':
                report.update(actual_ram_loader=True, actual_plt_hooks=7,
                              checks={key: True for key in probe_package.REQUIRED_S2_CHECKS})
            else:
                report['checks'] = {'synthetic_offline_fixture_only': True}
            (vm / 'report.json').write_text(json.dumps(report), encoding='utf-8')
            if name in probe_package.RUNTIME_GATES:
                (vm / 'candidate.json').write_text(json.dumps({'ram_build': build}), encoding='utf-8')
                for target, relative in probe_package.RUNTIME_FILES.items():
                    destination = vm / 'source-files' / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data[target])
        for target, relative in probe_package.COLLECTOR_FILES.items():
            destination = root / 'collector-vm/source-files' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data[target])
        controls_path = root / 'controls-menu' / 'report.json'
        controls_path.parent.mkdir()
        controls_path.write_text(json.dumps(dict(passed=True, private_vm_stopped=True,
            menu_item_count=7, checks={key: True for key in probe_package.REQUIRED_CONTROL_CHECKS},
            candidate_files={key: hashlib.sha256(data[key]).hexdigest()
                             for key in probe_package.CONTROL_FILES})), encoding='utf-8')
        (root / 'controls-menu-latest.json').write_text(
            json.dumps({'report': str(controls_path)}), encoding='utf-8')
        report = dict(profile='S2', abi=4, diagnostic=True, probe=True,
            diagnostic_runtime_schema=2, build=header[17], hook_count=7,
            s1_hook_count=5, s2_hook_count=7, camera_button_preview_menu_vm_verified=True,
            files=[dict(path=key, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                   for key, payload in data.items()])
        (root / 'probe-package-report.json').write_text(json.dumps(report), encoding='utf-8')
        with zipfile.ZipFile(root / 'nx300m-smartfilter-s2-probe.zip', 'w') as archive:
            for name, payload in data.items():
                archive.writestr(name, payload)
        # Existing ABI3 deliverables are sentinels, never valid replacement
        # evidence for --probe and never overwritten by read-only admission.
        (root / 'timing-package-report.json').write_bytes(b'ABI3 report sentinel\n')
        (root / 'nx300m-smartfilter-s2-timing.zip').write_bytes(b'ABI3 archive sentinel\n')
        (root / 'sd-timing').mkdir()
        (root / 'sd-timing/old.txt').write_bytes(b'ABI3 stage sentinel\n')
        with patch.object(probe_package, 'OUT', root), patch.object(probe_package, 'FILES', files), \
             patch.object(probe_package, 'MEDIA_SOURCE_ROOT', source_root):
            yield root


def edit_json(path, **changes):
    value = json.loads(path.read_text(encoding='utf-8'))
    value.update(changes)
    path.write_text(json.dumps(value), encoding='utf-8')


class OfflineProbeDeployment(unittest.TestCase):
    def run_probe(self, root, arguments=None):
        argv = ['deploy_smartfilter_card.py', '--check-only', '--s2', '--probe']
        if arguments is not None:
            argv = ['deploy_smartfilter_card.py', '--check-only', *arguments]
        output = io.StringIO()
        with patch.object(deploy, 'BASE', root), patch.object(sys, 'argv', argv), \
             patch.object(deploy.card, 'volume', side_effect=AssertionError('card access forbidden')), \
             patch.object(deploy.card, 'deploy', side_effect=AssertionError('card write forbidden')), \
             redirect_stdout(output):
            deploy.main()
        return output.getvalue()

    def test_probe_check_only_selects_separate_archive_without_card_access(self):
        with probe_fixture() as root:
            before = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in root.rglob('*') if p.is_file()}
            output = self.run_probe(root)
            self.assertIn('Checked 17 files in nx300m-smartfilter-s2-probe.zip', output)
            self.assertIn('no card accessed or written', output)
            after = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_probe_requires_explicit_s2_before_reading_report(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as raised:
                self.run_probe(Path(folder), ['--probe'])
            self.assertEqual(raised.exception.code, 2)

    def test_probe_and_timing_are_mutually_exclusive_before_reading_report(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as raised:
                self.run_probe(Path(folder), ['--s2', '--probe', '--timing'])
            self.assertEqual(raised.exception.code, 2)

    def test_probe_never_falls_back_to_the_old_abi3_report(self):
        with probe_fixture() as root:
            (root / 'probe-package-report.json').unlink()
            with self.assertRaises(FileNotFoundError):
                self.run_probe(root)
            self.assertEqual((root / 'timing-package-report.json').read_bytes(), b'ABI3 report sentinel\n')

    def test_probe_requires_exact_diagnostic_probe_abi_schema_flags(self):
        for key, value in (('diagnostic', False), ('diagnostic', 1), ('probe', False),
                           ('probe', 1), ('abi', 3), ('diagnostic_runtime_schema', 1)):
            with self.subTest(key=key, value=value), probe_fixture() as root:
                edit_json(root / 'probe-package-report.json', **{key: value})
                with self.assertRaisesRegex(RuntimeError, 'ABI4/schema2 diagnostic probe'):
                    self.run_probe(root)

    def test_probe_rejects_profile_and_slot_count_mix(self):
        for key, value, message in (('profile', 'S1', 'reports cannot be mixed'),
                                   ('s1_hook_count', 7, 'S1=5 and S2=7'),
                                   ('s2_hook_count', 5, 'S1=5 and S2=7')):
            with self.subTest(key=key), probe_fixture() as root:
                edit_json(root / 'probe-package-report.json', **{key: value})
                with self.assertRaisesRegex(RuntimeError, message):
                    self.run_probe(root)

    def test_probe_checks_all_five_reports_passed_and_stopped(self):
        for gate in probe_package.VM_GATES:
            for key, value in (('passed', False), ('private_vm_stopped', False), ('passed', 1)):
                with self.subTest(gate=gate, key=key, value=value), probe_fixture() as root:
                    edit_json(root / gate / 'report.json', **{key: value})
                    with self.assertRaisesRegex(RuntimeError, gate + ' verification has not passed'):
                        self.run_probe(root)

    def test_probe_and_collector_checks_must_be_nonempty_all_true(self):
        for gate in ('probe-vm', 'collector-vm'):
            for checks in ({}, {'x': False}, {'x': 1}, {'x': None}, []):
                with self.subTest(gate=gate, checks=checks), probe_fixture() as root:
                    edit_json(root / gate / 'report.json', checks=checks)
                    with self.assertRaisesRegex(RuntimeError, 'nonempty checks'):
                        self.run_probe(root)

    def test_s1_and_s2_actual_hook_counts_are_verified(self):
        for gate, count in (('ram-vm', 7), ('s2-vm', 5)):
            with self.subTest(gate=gate), probe_fixture() as root:
                edit_json(root / gate / 'report.json', actual_plt_hooks=count)
                with self.assertRaisesRegex(RuntimeError, 'PLT hooks'):
                    self.run_probe(root)

    def test_all_runtime_vm_build_and_abi_identities_match(self):
        for gate in probe_package.RUNTIME_GATES:
            for key, value in (('build', 3), ('abi', 3)):
                with self.subTest(gate=gate, key=key), probe_fixture() as root:
                    candidate = root / gate / 'candidate.json'
                    obj = json.loads(candidate.read_text(encoding='utf-8'))
                    obj['ram_build'][key] = value
                    candidate.write_text(json.dumps(obj), encoding='utf-8')
                    with self.assertRaisesRegex(RuntimeError, 'another module build/ABI'):
                        self.run_probe(root)

    def test_loader_and_blob_match_every_runtime_vm_source(self):
        for gate in probe_package.RUNTIME_GATES:
            for relative in probe_package.RUNTIME_FILES.values():
                with self.subTest(gate=gate, relative=relative), probe_fixture() as root:
                    (root / gate / 'source-files' / relative).write_bytes(b'changed after VM')
                    with self.assertRaisesRegex(RuntimeError, 'RAM-tested build: ' + gate):
                        self.run_probe(root)

    def test_collector_and_launcher_match_original_vm_bytes_without_normalizing(self):
        for relative in probe_package.COLLECTOR_FILES.values():
            with self.subTest(relative=relative), probe_fixture() as root:
                path = root / 'collector-vm/source-files' / relative
                path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
                with self.assertRaisesRegex(RuntimeError, 'collector-tested original'):
                    self.run_probe(root)

    def test_changed_archive_bytes_are_rejected_even_with_reports_still_true(self):
        with probe_fixture() as root:
            archive_path = root / 'nx300m-smartfilter-s2-probe.zip'
            with zipfile.ZipFile(archive_path) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}
            payloads['README.md'] = b'changed'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for name, payload in payloads.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(RuntimeError, 'Candidate bytes differ'):
                self.run_probe(root)

    def test_probe_inventory_must_include_launcher_and_collector(self):
        for key in probe_package.COLLECTOR_FILES:
            with self.subTest(key=key), probe_fixture() as root:
                path = root / 'probe-package-report.json'
                obj = json.loads(path.read_text(encoding='utf-8'))
                obj['files'] = [item for item in obj['files'] if item['path'] != key]
                path.write_text(json.dumps(obj), encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'Probe package inventory'):
                    self.run_probe(root)

    def test_duplicate_report_inventory_is_rejected(self):
        with probe_fixture() as root:
            path = root / 'probe-package-report.json'
            obj = json.loads(path.read_text(encoding='utf-8'))
            obj['files'].append(obj['files'][0])
            path.write_text(json.dumps(obj), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Duplicate candidate report'):
                self.run_probe(root)

    def test_controls_report_and_new_probe_checks_must_be_complete(self):
        for change in ({'passed': False}, {'private_vm_stopped': False}, {'checks': {}},
                       {'checks': {'old_only': True}}):
            with self.subTest(change=change), probe_fixture() as root:
                edit_json(root / 'controls-menu/report.json', **change)
                with self.assertRaisesRegex(RuntimeError, 'Controls-menu|controls-menu'):
                    self.run_probe(root)

    def test_controls_candidate_hash_is_rechecked_against_the_archive(self):
        with probe_fixture() as root:
            path = root / 'controls-menu/report.json'
            obj = json.loads(path.read_text(encoding='utf-8'))
            obj['candidate_files']['nx300m-mod/gui_preview.NX300M'] = '0' * 64
            path.write_text(json.dumps(obj), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Menu candidate differs'):
                self.run_probe(root)

    def test_future_menu_count_and_extra_true_check_do_not_break_admission(self):
        with probe_fixture() as root:
            path = root / 'controls-menu/report.json'
            obj = json.loads(path.read_text(encoding='utf-8'))
            obj['menu_item_count'] = 19
            obj['checks']['future_extra_behavior'] = True
            path.write_text(json.dumps(obj), encoding='utf-8')
            self.assertIn('no card accessed', self.run_probe(root))

    def test_new_start_guide_and_ram_manifest_are_separate_from_timing(self):
        self.assertEqual(deploy.start_guide(probe=True), 'SMARTFILTER-PROBE-START.md')
        self.assertEqual(deploy.start_guide(timing=True), 'SMARTFILTER-TIMING-START.md')
        self.assertEqual(deploy.start_guide(), 'SMARTFILTER-TEST-START.md')
        entries = deploy.runtime_manifest_entries(probe=True)
        self.assertTrue({'bin/smartfilter_probe_launcher.arm', 'smartfilter-probe-collector.sh',
                         'bin/smartfilter_loader.arm', 'lib/smartfilter_ram.bin'} <= entries)
        self.assertNotIn('README.md', entries)
        self.assertNotIn('smartfilter-probe-collector.sh', deploy.runtime_manifest_entries())


class OfflineProbePackageAdmission(unittest.TestCase):
    def test_validation_is_read_only_and_does_not_build_an_archive(self):
        with probe_fixture() as root:
            with patch.object(probe_package.zipfile, 'ZipFile', side_effect=AssertionError('no packaging allowed')):
                build, reports, controls, payloads = probe_package.validate_candidate()
            self.assertEqual(build['abi'], 4)
            self.assertEqual(set(reports), set(probe_package.VM_GATES))
            self.assertEqual(len(payloads), 17)
            self.assertEqual(controls, root / 'controls-menu/report.json')
            self.assertFalse((root / 'sd-probe').exists())
            self.assertEqual((root / 'sd-timing/old.txt').read_bytes(), b'ABI3 stage sentinel\n')

    def test_old_abi_build_and_blob_are_refused(self):
        for change in ({'abi': 3}, {'s1_hook_count': 7}, {'s2_hook_count': 5}, {'blob_bytes': 95}):
            with self.subTest(change=change), probe_fixture() as root:
                edit_json(root / 'ram-build-report.json', **change)
                with self.assertRaisesRegex(ValueError, 'ABI4'):
                    probe_package.validate_candidate()

    def test_blob_header_identity_and_size_are_refused(self):
        for index, value in ((0, 0), (1, 3), (2, 95), (14, 5), (16, 17), (17, 1)):
            with self.subTest(index=index), probe_fixture() as root:
                key = 'nx300m-mod/lib/smartfilter_ram.bin'
                payload = bytearray(probe_package.FILES[key].read_bytes())
                struct.pack_into('<I', payload, index * 4, value)
                probe_package.FILES[key].write_bytes(payload)
                for gate in probe_package.RUNTIME_GATES:
                    (root / gate / 'source-files/smartfilter_ram.bin').write_bytes(payload)
                with self.assertRaisesRegex(ValueError, 'ABI4 blob'):
                    probe_package.validate_candidate()

    def test_every_vm_gate_must_be_completed_before_packaging(self):
        for gate in probe_package.VM_GATES:
            with self.subTest(gate=gate), probe_fixture() as root:
                edit_json(root / gate / 'report.json', private_vm_stopped=False)
                with self.assertRaisesRegex(ValueError, gate):
                    probe_package.validate_candidate()
                self.assertFalse((root / 'sd-probe').exists())

    def test_changed_runtime_and_original_collector_sources_block_packaging(self):
        for gate, relative in ((gate, relative) for gate in probe_package.RUNTIME_GATES
                               for relative in probe_package.RUNTIME_FILES.values()):
            with self.subTest(gate=gate, relative=relative), probe_fixture() as root:
                (root / gate / 'source-files' / relative).write_bytes(b'untested')
                with self.assertRaisesRegex(ValueError, 'differs from ' + gate):
                    probe_package.validate_candidate()
        for relative in probe_package.COLLECTOR_FILES.values():
            with self.subTest(relative=relative), probe_fixture() as root:
                path = root / 'collector-vm/source-files' / relative
                path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
                with self.assertRaisesRegex(ValueError, 'Collector ORIGINAL'):
                    probe_package.validate_candidate()

    def test_unpopulated_probe_checks_are_not_forged_or_accepted(self):
        for gate in ('probe-vm', 'collector-vm'):
            with self.subTest(gate=gate), probe_fixture() as root:
                edit_json(root / gate / 'report.json', checks={})
                with self.assertRaisesRegex(ValueError, 'nonempty checks'):
                    probe_package.validate_candidate()
                self.assertFalse((root / 'sd-probe').exists())

    def test_menu_candidate_drift_blocks_packaging(self):
        with probe_fixture():
            probe_package.FILES['nx300m-mod/gui_preview.NX300M'].write_bytes(b'changed menu')
            with self.assertRaisesRegex(ValueError, 'Menu candidate differs'):
                probe_package.validate_candidate()


class OfflineRealFilesystemAdmission(unittest.TestCase):
    def both_refuse(self, root, message):
        with self.assertRaisesRegex((ValueError, FileNotFoundError), message):
            probe_package.validate_candidate()
        with self.assertRaisesRegex(RuntimeError, message):
            OfflineProbeDeployment().run_probe(root)

    def test_missing_real_fat_evidence_blocks_package_and_deployment(self):
        with probe_fixture() as root:
            (root / 'media-fs/report.json').unlink()
            self.both_refuse(root, 'report.json')

    def test_skip_or_cleanup_failure_is_not_a_real_fat_pass(self):
        for field in ('passed', 'private_mount_namespace', 'all_mounts_removed', 'loop_released'):
            with self.subTest(field=field), probe_fixture() as root:
                path = root / 'media-fs/report.json'
                obj = json.loads(path.read_text())
                obj['filesystem_run'][field] = False
                path.write_text(json.dumps(obj))
                self.both_refuse(root, 'Real FAT32')
        with probe_fixture() as root:
            path = root / 'media-fs/report.json'
            obj = json.loads(path.read_text())
            obj['filesystem_run']['fat32']['tested'] = False
            path.write_text(json.dumps(obj))
            self.both_refuse(root, 'Real FAT32')

    def test_equal_uid_or_mocked_filesystem_is_rejected(self):
        for mock in (False, True):
            with self.subTest(mock=mock), probe_fixture() as root:
                path = root / 'media-fs/report.json'
                obj = json.loads(path.read_text())
                fat = obj['filesystem_run']['fat32']
                if mock:
                    fat['mocked_fs_or_uid'] = True
                else:
                    fat['observations'][0]['uid'] = fat['observations'][0]['euid']
                path.write_text(json.dumps(obj))
                self.both_refuse(root, 'Real FAT32')

    def test_source_and_candidate_drift_are_rejected(self):
        with probe_fixture() as root:
            path = root / 'media-sources/runtime/smartfilter_media.h'
            path.write_bytes(b'changed policy after real FS test')
            self.both_refuse(root, 'tested source differs')
        with probe_fixture() as root:
            path = root / 'media-fs/report.json'
            obj = json.loads(path.read_text())
            obj['candidates']['smartfilter_probe_launcher.arm'] = '0' * 64
            path.write_text(json.dumps(obj))
            self.both_refuse(root, 'candidate bytes differ')

    def test_fs_report_does_not_replace_arm_build_provenance(self):
        for change in ({'control_sources': {}}, {'control_artifacts': {}}):
            with self.subTest(change=change), probe_fixture() as root:
                edit_json(root / 'ram-build-report.json', **change)
                self.both_refuse(root, 'control build provenance')
        with probe_fixture() as root:
            path = root / 'ram-build-report.json'
            obj = json.loads(path.read_text())
            obj['control_sources']['runtime/smartfilter_media.h'] = '0' * 64
            path.write_text(json.dumps(obj))
            self.both_refuse(root, 'Control source changed since ARM build')


if __name__=='__main__':
    unittest.main(verbosity=2)
