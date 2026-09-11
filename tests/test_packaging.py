import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_support import data_root, open_native, open_with
from library_core import Library
from disk_catalog import sync_catalog


class PackagingTests(unittest.TestCase):
    def test_user_data_path_not_bundle_directory(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': '/user/local', 'XDG_DATA_HOME': '/user/share'}, clear=True), patch('sys.frozen', True, create=True), patch('platform_support.Path.home', return_value=Path('/user/home')):
            with patch('sys.platform', 'win32'):
                self.assertEqual(data_root(), Path('/user/local/LiteratureShelf'))
            with patch('sys.platform', 'linux'):
                self.assertEqual(data_root(), Path('/user/share/LiteratureShelf'))
            with patch('sys.platform', 'darwin'):
                self.assertEqual(data_root(), Path.home() / 'Library/Application Support/LiteratureShelf')

    def test_native_openers_use_argument_arrays(self):
        with patch('platform_support.subprocess.Popen') as run:
            with patch('sys.platform', 'darwin'):
                open_native('/paper with spaces.pdf')
                self.assertEqual(run.call_args.args[0][0], 'open')
                open_native('/api.json', edit=True)
                self.assertEqual(run.call_args.args[0][:2], ['open', '-t'])
            with patch('sys.platform', 'linux'):
                open_native('/paper with spaces.pdf')
                self.assertEqual(run.call_args.args[0][0], 'xdg-open')
        with patch('platform_support.os.startfile', create=True) as run, patch('sys.platform', 'win32'):
            open_native('/paper.pdf')
            run.assert_called_once()

    def test_first_library_is_offline_and_disk_links_preserve_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp) / 'library.sqlite3')
            self.assertEqual(lib.settings()['mode'], '本地规则')
            original = Path(tmp) / 'original.txt'
            original.write_text('original', 'utf-8')
            ident, _ = lib.add(original)
            root = sync_catalog(lib)
            entries = list(root.rglob('*.url')) if sys.platform == 'win32' else [p for p in root.rglob('*') if p.is_symlink()]
            self.assertEqual(len(entries), 1)
            if sys.platform != 'win32':
                self.assertEqual(entries[0].resolve(), original)
            lib.remove(ident)
            sync_catalog(lib)
            self.assertEqual(original.read_text(), 'original')
            self.assertFalse(entries[0].exists())


if __name__ == '__main__':
    unittest.main()
