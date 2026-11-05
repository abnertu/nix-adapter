"""Unit tests for Files and configuration parsing helpers."""

import os
import tempfile
import unittest
from pathlib import Path

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
        self.assertEqual(
            assignments(text, source="/etc/test"), {"FOO": "bar", "BAZ": "hello world"}
        )

    def test_assignments_invalid(self):
        with self.assertRaises(Conflict):
            assignments("invalid line", source="/etc/test")

    def test_assignment_errors_name_the_key_but_never_the_value(self):
        text = "TOKEN=first\nTOKEN=glrt-secret-value\n"
        with self.assertRaises(Conflict) as raised:
            assignments(text, source="/etc/default/runner")
        message = str(raised.exception)
        self.assertIn("TOKEN", message)
        self.assertIn("/etc/default/runner:2", message)
        self.assertNotIn("glrt-secret-value", message)

    def test_replace_keys(self):
        text = "FOO=bar\nKEEP=me\n"
        result = replace_keys(
            text, {"FOO": "new_val", "ADD": "added"}, source="/etc/test"
        )
        self.assertIn("FOO=new_val\n", result)
        self.assertIn("KEEP=me\n", result)
        self.assertIn("ADD=added\n", result)

    def test_locale_gen(self):
        initial = "en_US.UTF-8 UTF-8\n"
        result = locale_gen(
            initial,
            ["zh_TW.UTF-8"],
            block="nix-config locales",
            source="/etc/locale.gen",
        )
        self.assertIn("# BEGIN nix-config locales", result)
        self.assertIn("zh_TW.UTF-8 UTF-8", result)
        self.assertIn("# END nix-config locales", result)

    def test_ini_parsing(self):
        text = "[Section1]\nkey1 = value1\n[Section2]\nkey2 = value2\n"
        parsed = ini(text, source="/etc/test.conf")
        self.assertEqual(parsed[("Section1", "key1")], "value1")
        self.assertEqual(parsed[("Section2", "key2")], "value2")


class FilesOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.files = Files(
            root=self.root,
            identity=(os.getuid(), os.getgid()),
            pending_template="/var/lib/test/{action}.pending",
        )

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


class DropinTests(unittest.TestCase):
    """Files.dropin checks every source systemd reads, in systemd's order."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = Files(self.root, (os.getuid(), os.getgid()))

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name: str, text: str) -> Path:
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_sources_follow_systemd_precedence(self):
        self.put("/usr/lib/systemd/journald.conf", "[Journal]\n")
        self.put("/etc/systemd/journald.conf", "[Journal]\n")
        self.put("/usr/lib/systemd/journald.conf.d/10-vendor.conf", "[Journal]\n")
        self.put("/run/systemd/journald.conf.d/10-vendor.conf", "[Journal]\n")
        self.put("/usr/lib/systemd/journald.conf.d/20-other.conf", "[Journal]\n")
        self.assertEqual(
            self.files.systemd_sources("journald"),
            [
                "/etc/systemd/journald.conf",
                "/run/systemd/journald.conf.d/10-vendor.conf",
                "/usr/lib/systemd/journald.conf.d/20-other.conf",
            ],
        )

    def test_disagreeing_source_names_itself_and_both_values(self):
        self.put(
            "/etc/systemd/journald.conf.d/10-admin.conf",
            "[Journal]\nStorage=persistent\n",
        )
        with self.assertRaises(Conflict) as raised:
            self.files.dropin(
                "journald", "[Journal]\nStorage=volatile\n", file_name="60-x.conf"
            )
        message = str(raised.exception)
        self.assertIn("/etc/systemd/journald.conf.d/10-admin.conf", message)
        self.assertIn("Storage=persistent (expected: volatile)", message)

    def test_masked_sources_and_the_target_itself_are_ignored(self):
        self.put("/etc/systemd/journald.conf.d/60-x.conf", "[Journal]\nStorage=old\n")
        masked = self.root / "etc/systemd/journald.conf.d/10-masked.conf"
        masked.symlink_to("/dev/null")
        target = self.files.dropin(
            "journald", "[Journal]\nStorage=volatile\n", file_name="60-x.conf"
        )
        self.assertEqual(target, "/etc/systemd/journald.conf.d/60-x.conf")


if __name__ == "__main__":
    unittest.main()
