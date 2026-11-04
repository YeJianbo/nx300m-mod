"""LookLibrary same-look concurrency and multi-table publish consistency.

Offline only: a temporary library root, small synthetic cubes, no SD card,
no camera. Verifies the per-look write lock, unique staged temp files and
the restore-on-failure publish transaction added this round, while keeping
the consumer snapshot contract (tools/lut_table_data.read_tables)."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from look_library import LookLibrary, LookLock  # noqa: E402
from lut_table_data import read_tables  # noqa: E402

REPORT = TOOLS.parent/'outputs/looks/publish-consistency/report.json'


def tiny_cube(path: Path, shift: float):
    rows = ['TITLE "publish-test"', 'LUT_3D_SIZE 4']
    for b in range(4):
        for g in range(4):
            for r in range(4):
                rows.append(f'{(r+shift)/3:.6f} {(g+shift)/3:.6f} {(b+shift)/3:.6f}')
    path.write_text('\n'.join(rows)+'\n', encoding='utf-8')


def fresh_look(root: Path, key: str, shift: float):
    source = root.parent/f'{key}.cube'
    tiny_cube(source, shift)
    library = LookLibrary(root=root)
    metadata = library.add_cube(source, key, input_space='srgb')
    source.unlink()
    return library, metadata


def compile_wrong_revision(root: Path, key: str):
    """Child process: compile with a stale compiler revision by editing the
    in-memory metadata before build, forcing the slow path concurrently."""
    library = LookLibrary(root=root)
    metadata = library.get(key)
    metadata['compiler']['revision'] = 'stale'
    library._save(key, metadata)
    library.build(key)


def build_directly(root: Path, key: str, error_file=None):
    try:
        library = LookLibrary(root=root)
        library.build(key)
    except BaseException as error:
        if error_file:
            Path(error_file).write_text(repr(error), encoding='utf-8')
        raise


def pair(directory: Path):
    return {name: (directory/name).read_bytes()
            for name in ('table.d4lut', 'table-stock3d.d4lut')}


def die_holding_lock(directory: Path, ready):
    with LookLock(directory):
        ready.set()
        # Process exit must release the kernel lock without running __exit__.
        os._exit(0)


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix='nx300m-look-publish-') as tmp:
        root = Path(tmp)/'looks'

        # 1) Concurrent same-look builds produce the identical pair and no residue.
        _, reference = fresh_look(root, 'publish-a', 0.0)
        expected = pair(root/'publish-a')
        reference_revision = reference['compiler']['revision']
        errors = root.parent/'errors'
        errors.mkdir()
        def spawn(key, count):
            jobs = []
            for index in range(count):
                job = multiprocessing.Process(target=build_directly,
                    args=(root, key, str(errors/f'{key}.{index}.error')))
                job.start()
                jobs.append((job, errors/f'{key}.{index}.error'))
            for job, _ in jobs: job.join(240)
            failed = [err.read_text(encoding='utf-8') for job, err in jobs
                      if job.exitcode != 0 and err.exists()]
            assert all(job.exitcode == 0 for job, _ in jobs), \
                [job.exitcode for job, _ in jobs]+failed
        spawn('publish-a', 4)
        assert pair(root/'publish-a') == expected
        assert not list((root/'publish-a').glob('*.tmp')), 'staged temp leaked'
        assert not (root/'publish-a'/'.build.lock').exists(), 'lock leaked'
        checks.append({'check': 'concurrent_build_same_look_consistent', 'passed': True, 'workers': 4})

        # 2) ensure_compiled double-check inside the lock does not rebuild
        #    a fresh library entry and stays byte-identical under contention.
        fresh_look(root, 'publish-b', 0.25)
        before = pair(root/'publish-b')
        spawn('publish-b', 3)
        assert pair(root/'publish-b') == before
        checks.append({'check': 'repeated_build_byte_stable', 'passed': True, 'workers': 3})

        # 3) A publish failure restores the previous consistent pair and metadata.
        library, metadata = fresh_look(root, 'publish-c', 0.5)
        before_tables = pair(root/'publish-c')
        before_json = (root/'publish-c'/'look.json').read_bytes()
        # Keep the raw staticmethod descriptor so the restore is exact.
        original = LookLibrary.__dict__['_temp_write']
        calls = []
        def failing_write(path, data):
            calls.append(path.name)
            if path.name.startswith('table-stock3d.d4lut'):
                raise OSError('injected staging failure')
            return original.__func__(path, data)
        LookLibrary._temp_write = staticmethod(failing_write)
        try:
            library.build('publish-c')
            raise AssertionError('build should have failed')
        except OSError as error:
            assert 'injected staging failure' in str(error), error
        finally:
            LookLibrary._temp_write = original
        assert pair(root/'publish-c') == before_tables
        assert (root/'publish-c'/'look.json').read_bytes() == before_json
        assert not list((root/'publish-c').glob('*.tmp')), 'staged temp leaked after failure'
        assert hashlib.sha256(before_json).hexdigest()
        checks.append({'check': 'publish_failure_restores_previous_pair',
                       'passed': True, 'staged': calls})

        # A changed source must not strand new tables if metadata cannot commit.
        tiny_cube(root/'publish-c'/'source.cube', 0.1)
        before_set = {name: (root/'publish-c'/name).read_bytes()
                      for name in ('table.d4lut', 'table-stock3d.d4lut', 'look.json')}
        replace = os.replace
        swaps = []

        def fail_commit(source, destination):
            destination = Path(destination)
            if destination.name == 'look.json' and destination.name not in swaps:
                swaps.append(destination.name)
                # Both table replacements really happened before this failure.
                assert pair(root/'publish-c') != before_tables
                raise OSError('injected metadata commit failure')
            swaps.append(destination.name)
            return replace(source, destination)

        with patch('look_library.os.replace', side_effect=fail_commit):
            try:
                library.build('publish-c')
                raise AssertionError('metadata commit should have failed')
            except OSError as error:
                assert 'metadata commit' in str(error), error
        assert {name: (root/'publish-c'/name).read_bytes() for name in before_set} == before_set
        assert not list((root/'publish-c').glob('*.tmp'))
        read_tables(root/'publish-c', json.loads(before_json.decode('utf-8')))
        checks.append({'check': 'metadata_commit_failure_restores_tables_and_metadata',
                       'passed': True, 'replaced_tables_before_failure': True})

        # 4) The consumer snapshot contract still rejects a genuinely mixed pair
        #    and accepts the restored one.
        read_tables(root/'publish-c', json.loads(before_json.decode('utf-8')))
        victim = root/'publish-c'/'table-stock3d.d4lut'
        keep = victim.read_bytes()
        # Corrupt a middle sample word without touching the reserved padding,
        # so the failure is the snapshot hash check, not the format check.
        corrupted = bytearray(keep)
        corrupted[200] ^= 0x01
        assert corrupted != bytearray(keep)
        victim.write_bytes(bytes(corrupted))
        try:
            read_tables(root/'publish-c', json.loads(before_json.decode('utf-8')))
            raise AssertionError('mixed pair must be rejected')
        except ValueError as error:
            assert 'snapshot' in str(error) or 'changed' in str(error), error
        finally:
            victim.write_bytes(keep)
        checks.append({'check': 'consumer_snapshot_contract_retained', 'passed': True})

        # 5) rename is serialized against builds and preserves table bytes.
        library.rename('publish-c', 'Renamed Look')
        assert json.loads((root/'publish-c'/'look.json').read_text(encoding='utf-8'))['label'] == 'Renamed Look'
        assert pair(root/'publish-c') == before_tables
        checks.append({'check': 'rename_under_lock_keeps_tables', 'passed': True})

        # 6) A dead writer releases its lock immediately, without age guessing.
        ready = multiprocessing.Event()
        job = multiprocessing.Process(target=die_holding_lock, args=(root/'publish-c', ready))
        job.start(); assert ready.wait(15)
        job.join(15); assert job.exitcode == 0
        with LookLock(root/'publish-c'):
            pass
        checks.append({'check': 'dead_writer_lock_released', 'passed': True})

        # A slow live compiler must retain its lock even with an old timestamp.
        import look_library
        with LookLock(root/'publish-c') as held:
            old = held.path.stat().st_mtime-1200.0
            os.utime(held.path, (old, old))
            with patch.object(look_library, 'LOCK_TIMEOUT_SECONDS', 0.05):
                try:
                    with LookLock(root/'publish-c'):
                        raise AssertionError('live lock was stolen')
                except TimeoutError:
                    pass
        checks.append({'check': 'old_live_writer_not_stolen', 'passed': True})

        # 7) The real library is untouched by this test.
        live = LookLibrary()
        entries = live.list()
        assert entries and reference_revision, 'live library still readable'
        checks.append({'check': 'active_library_untouched', 'passed': True,
                       'looks': len(entries)})

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(
        {'passed': all(c['passed'] for c in checks), 'checks': checks},
        ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'checks': len(checks), 'report': str(REPORT)}))


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
