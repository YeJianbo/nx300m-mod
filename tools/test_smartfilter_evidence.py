"""Host-only regression checks for loss-detecting SmartFilter UART collection."""
import base64
import gzip
import hashlib
import io
from pathlib import Path
import re
import random
import tarfile
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import probe_smartfilter_vm as probe


class EvidenceTransfer(unittest.TestCase):
    def archive(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as package:
            payload = random.Random(42).randbytes(32768)
            info = tarfile.TarInfo('mfw-results/payload.bin')
            info.size = len(payload)
            package.addfile(info, io.BytesIO(payload))
        return gzip.compress(output.getvalue(), mtime=0), payload

    def collect(self, archive, *, drop_once=False, corrupt_always=False, wrong_total=False, stale=False):
        attempts = {}
        def call(action, command):
            self.assertEqual(action, 'exec')
            if 'ARCHIVE_BYTES=' in command:
                digest = '0'*64 if wrong_total else hashlib.sha256(archive).hexdigest()
                return dict(exit_code=0,output=f'{digest}  /tmp/smartfilter-evidence.tar.gz\r\n'
                    f'ARCHIVE_BYTES={len(archive)}\r\n')
            offset = int(re.search(r'skip=(\d+)', command).group(1))*4096
            data = archive[offset:offset+4096]
            digest = hashlib.sha256(data).hexdigest()
            attempts[offset] = attempts.get(offset, 0)+1
            lines = textwrap.wrap(base64.b64encode(data).decode(), 76)
            if corrupt_always or (drop_once and attempts[offset] == 1):
                lines = lines[1:]
            return dict(exit_code=0,output=f'{digest}  /tmp/smartfilter-evidence.chunk\r\n'+
                '\r\n'.join(lines)+'\r\n')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            if stale:
                (root/'guest/mfw-results').mkdir(parents=True)
                (root/'guest/mfw-results/stale.txt').write_text('previous run')
            with patch.object(probe, 'OUT', root), patch.object(probe, 'call', call):
                guest = probe.collect()
            if stale:
                self.assertFalse((guest/'stale.txt').exists())
                preserved=list(root.glob('guest-before-*/mfw-results/stale.txt'))
                self.assertEqual(len(preserved),1)
                self.assertEqual(preserved[0].read_text(),'previous run')
            self.assertEqual((root/'guest-evidence.tar.gz').read_bytes(), archive)
            return (guest/'payload.bin').read_bytes(), attempts

    def test_clean_archive(self):
        archive, payload = self.archive()
        actual, attempts = self.collect(archive)
        self.assertEqual(actual, payload)
        self.assertTrue(all(value == 1 for value in attempts.values()))

    def test_old_evidence_is_preserved_but_not_merged(self):
        archive, payload = self.archive()
        actual, _ = self.collect(archive, stale=True)
        self.assertEqual(actual, payload)

    def test_lost_base64_line_is_retried(self):
        archive, payload = self.archive()
        actual, attempts = self.collect(archive, drop_once=True)
        self.assertEqual(actual, payload)
        self.assertTrue(all(value == 2 for value in attempts.values()))

    def test_persistent_corruption_is_rejected(self):
        archive, _ = self.archive()
        with self.assertRaisesRegex(RuntimeError, 'chunk integrity'):
            self.collect(archive, corrupt_always=True)

    def test_whole_archive_hash_is_checked(self):
        archive, _ = self.archive()
        with self.assertRaisesRegex(ValueError, 'guest SHA256'):
            self.collect(archive, wrong_total=True)

    def test_guest_crc_failure_is_not_ignored(self):
        archive, _ = self.archive()
        damaged = archive[:-8]+bytes([archive[-8]^1])+archive[-7:]
        with self.assertRaises(gzip.BadGzipFile):
            self.collect(damaged)


if __name__ == '__main__':
    unittest.main()
