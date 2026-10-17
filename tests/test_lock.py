"""Unit tests for operation_lock."""

import tempfile
import unittest
from pathlib import Path

from nix_adapter.exceptions import Conflict
from nix_adapter.lock import operation_lock


class LockTests(unittest.TestCase):
    def test_exclusive_lock_prevents_concurrent_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "test.lock"
            with (
                operation_lock(lock_path),
                self.assertRaises(Conflict),
                operation_lock(lock_path, timeout=0),
            ):
                pass

    def test_reentrant_after_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "test.lock"
            with operation_lock(lock_path):
                pass
            with operation_lock(lock_path):
                pass


if __name__ == "__main__":
    unittest.main()
