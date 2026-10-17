"""Unit tests for Files and configuration parsing helpers."""

import os
from pathlib import Path
import tempfile
import unittest

from nix_adapter import (
    Conflict,
    Files,
    assignments,
    ini,
    locale_gen,
    replace_keys,
)


class FilesParsingTests(unittest.TestCase):
    def test_assignments_valid(self):
        text = 'FOO=bar\n# comment\nBAZ="hello world"\n'
        self.assertEqual(assignments(text), {"FOO": "bar", "BAZ": "hello world"})

    def test_assignments_invalid(self):
        with self.assertRaises(Conflict):
            assignments("invalid line")

    def test_replace_keys(self):
        text = "FOO=bar\nKEEP=me\n"
        result = replace_keys(text, {"FOO": "new_val", "ADD": "added"})
        self.assertIn("FOO=new_val\n", result)
        self.assertIn("KEEP=me\n", result)
        self.assertIn("ADD=added\n", result)

    def test_locale_gen(self):
        initial = "en_US.UTF-8 UTF-8\n"
        result = locale_gen(initial, ["zh_TW.UTF-8"])
        self.assertIn("# BEGIN nix-config locales", result)
        self.assertIn("zh_TW.UTF-8 UTF-8", result)
        self.assertIn("# END nix-config locales", result)

    def test_ini_parsing(self):
        text = "[Section1]\nkey1 = value1\n[Section2]\nkey2 = value2\n"
        parsed = ini(text)
        self.assertEqual(parsed[("Section1", "key1")], "value1")
        self.assertEqual(parsed[("Section2", "key2")], "value2")


class FilesOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.files = Files(root=self.root, identity=(os.getuid(), os.getgid()))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_and_read(self):
        file_path = "/etc/test.conf"
        content = "hello world\n"
        written = self.files.write(file_path, content)
        self.assertTrue(written)
        self.assertEqual(self.files.read(file_path), content)

        # Writing exact same content returns False (no change)
        self.assertFalse(self.files.write(file_path, content))

    def test_marker_lifecycle(self):
        action = "testing"
        self.assertFalse(self.files.pending(action))
        self.files.mark(action)
        self.assertTrue(self.files.pending(action))
        self.files.clear(action)
        self.assertFalse(self.files.pending(action))


class FilesDiagnosticsTests(unittest.TestCase):
    def test_path_errors_name_the_offending_system_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = Files(root, (os.getuid(), os.getgid()))
            (root / "etc").mkdir()
            (root / "etc").chmod(0o777)
            with self.assertRaisesRegex(
                Conflict, r"system path: /etc \(owner \d+, mode 0777\)"
            ):
                files.path("/etc/demo.conf")
            (root / "etc").chmod(0o755)
            (root / "etc/link").symlink_to(root)
            with self.assertRaisesRegex(
                Conflict, "symlink in managed system path: /etc/link"
            ):
                files.path("/etc/link/demo.conf")
            with self.assertRaisesRegex(
                Conflict, "invalid private system path: relative"
            ):
                files.path("relative")


if __name__ == "__main__":
    unittest.main()
