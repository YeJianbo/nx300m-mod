"""Check shared host/WSL path contracts without mounting shares or running ADB."""
import os
from pathlib import Path, PureWindowsPath
import subprocess
import unittest
from unittest.mock import patch

import wsl_paths
from quickshare_link import linux_path
from stock_gallery_runtime import linux_source


class PathContracts(unittest.TestCase):
    def test_absolute_paths(self):
        cases = [
            (r'D:\camera\two words\中文.c', '/mnt/d/camera/two words/中文.c'),
            (PureWindowsPath(r'C:\tools\..\source.c'), '/mnt/c/source.c'),
            (r'\\?\D:\camera\source.c', '/mnt/d/camera/source.c'),
            ('/opt/camera/中文.c', '/opt/camera/中文.c'),
            (r'/opt/a\b', r'/opt/a\b'),
            (r'\\wsl.localhost\Debian\home\user\source.c', './local/developer/source.c'),
            (r'\\wsl$\Debian\home\user\source.c', './local/developer/source.c'),
            (r'\\?\UNC\wsl.localhost\Debian\home\user', './local/developer'),
            (r'\\wsl$\Debian', '/'),
            (Path('/opt/camera/source.c'), '/opt/camera/source.c'),
        ]
        with patch.object(wsl_paths.subprocess, 'run') as run:
            for source, target in cases:
                with self.subTest(source=source):
                    self.assertEqual(wsl_paths.wsl_path(source), target)
            run.assert_not_called()

    def test_relative_and_invalid_paths(self):
        self.assertEqual(wsl_paths.wsl_path('source.c'),
                         wsl_paths.wsl_path(Path.cwd() / 'source.c'))
        for value in ('', 'C:source.c', '\0', r'\\server'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wsl_paths.wsl_path(value)
        with self.assertRaises(ValueError):
            wsl_paths.wsl_path(r'\\wsl$\Debian\tmp', distro='Ubuntu')

    def test_network_converter(self):
        wsl_paths._network_path.cache_clear()
        value = r'\\server\share\two words'
        result = subprocess.CompletedProcess([], 0, '/mnt/shared/two words\n', '')
        with patch.object(wsl_paths.subprocess, 'run', return_value=result) as run:
            self.assertEqual(wsl_paths.wsl_path(value, distro='Debian'), '/mnt/shared/two words')
            self.assertEqual(wsl_paths.wsl_path(value, distro='Debian'), '/mnt/shared/two words')
            run.assert_called_once()
            command = run.call_args.args[0]
            expected = (['wsl.exe', '--distribution', 'Debian', '--exec'] if os.name == 'nt' else [])
            self.assertEqual(command, expected + ['wslpath', '-a', '-u', value])
        wsl_paths._network_path.cache_clear()
        with patch.object(wsl_paths.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'unavailable')):
            with self.assertRaisesRegex(ValueError, 'exit 1: unavailable'):
                wsl_paths.wsl_path(value)

    def test_argument_and_metadata_adapters(self):
        for prefix in ('', '-L', '-Wl,--version-script=', '-Wl,-T,'):
            self.assertEqual(linux_path(prefix + r'D:\source\test.c'), prefix + '/mnt/d/source/test.c')
        for value in ('-static', '-Lrelative', '-Wl,-T,relative.ld', 'relative.o', '/tmp/object.o'):
            self.assertEqual(linux_path(value), value)
        self.assertEqual(linux_source(''), Path(''))
        self.assertEqual(linux_source(r'D:\images\test.jpg'), Path('/mnt/d/images/test.jpg'))


if __name__ == '__main__':
    unittest.main()
