"""Unit tests for BaseServiceAdapter."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from nix_adapter.exceptions import Conflict
from nix_adapter.service import BaseServiceAdapter


class ServiceAdapterTests(unittest.TestCase):
    def test_path_validation(self):
        adapter = BaseServiceAdapter(
            desired={
                "valid": "/etc/foo",
                "traversal": "/etc/../bar",
                "relative": "relative",
            }
        )
        self.assertEqual(adapter.path("valid"), Path("/etc/foo"))
        with self.assertRaises(Conflict):
            adapter.path("traversal")
        with self.assertRaises(Conflict):
            adapter.path("relative")

    def test_validate_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_path = root / "config.toml"
            file_path.write_text("ok")
            file_path.chmod(0o644)

            adapter = BaseServiceAdapter(desired={"config": "/config.toml"}, root=root)
            validated = adapter.validate_file("config")
            self.assertEqual(validated, file_path)

            # Invalid permissions (world writable)
            file_path.chmod(0o666)
            with self.assertRaises(Conflict):
                adapter.validate_file("config")

    def test_write_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit_path = root / "etc/systemd/system/test.service"
            receipt = root / "var/lib/test.ready"
            pending = root / "var/lib/test.pending"
            receipt.parent.mkdir(parents=True)

            adapter = BaseServiceAdapter(desired={}, root=root)
            # First write: creates unit, touches pending, returns True
            changed = adapter.write_unit(
                unit_path, "[Service]\nExecStart=/bin/test\n", receipt, pending
            )
            self.assertTrue(changed)
            self.assertTrue(unit_path.exists())
            self.assertTrue(pending.exists())

            # Mark ready
            receipt.touch()

            # Second write with same content: returns False
            changed_again = adapter.write_unit(
                unit_path, "[Service]\nExecStart=/bin/test\n", receipt, pending
            )
            self.assertFalse(changed_again)

    def test_ensure_system_account(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.returncode = 1  # user and group don't exist

        adapter = BaseServiceAdapter(desired={}, runner=mock_runner)
        adapter.ensure_system_account("mysvc", create=True)

        mock_runner.run.assert_any_call("groupadd", "--system", "mysvc")
        mock_runner.run.assert_any_call(
            "useradd",
            "--system",
            "--gid",
            "mysvc",
            "--home-dir",
            "/var/lib/mysvc",
            "--shell",
            "/usr/bin/nologin",
            "mysvc",
        )

    def test_base_adapter_contract(self):
        import os

        from nix_adapter.base import BaseAdapter
        from nix_adapter.files import Files

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = Files(root=root, identity=(os.getuid(), os.getgid()))
            adapter = BaseAdapter(desired={"foo": "bar"}, root=root, files=files)
            self.assertEqual(adapter.desired, {"foo": "bar"})
            self.assertEqual(adapter.root, root)
            self.assertIs(adapter.native, adapter.runner)

            # Test write helper
            written = adapter.write("/etc/sample.conf", "content\n", "sample")
            self.assertTrue(written)
            self.assertEqual(adapter.updates, 1)
            self.assertTrue(adapter.files.pending("sample"))

            # Idempotent write
            written_again = adapter.write("/etc/sample.conf", "content\n", "sample")
            self.assertFalse(written_again)
            self.assertEqual(adapter.updates, 1)

            # Test native setter
            mock_runner = MagicMock()
            adapter.native = mock_runner
            self.assertIs(adapter.native, mock_runner)
            self.assertIs(adapter.runner, mock_runner)

            # Preflight and converge default stubs
            self.assertIsNone(adapter.preflight())
            self.assertIsNone(adapter.converge())


if __name__ == "__main__":
    unittest.main()
