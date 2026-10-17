"""Unit tests for nix_adapter.io primitives."""

import os
import tempfile
import unittest
from pathlib import Path

from nix_adapter.exceptions import Conflict
from nix_adapter.io import (
    atomic_write,
    ensure_directory,
    read_managed,
    remove_managed_file,
)


class IoTests(unittest.TestCase):
    def test_links_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "file"
            sentinel.write_text("unchanged")
            link = root / "link"
            link.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(Conflict):
                atomic_write(
                    link / "file",
                    "changed",
                    mode=0o600,
                    uid=os.getuid(),
                    gid=os.getgid(),
                )

            with self.assertRaises(Conflict):
                ensure_directory(link, mode=0o700, uid=os.getuid(), gid=os.getgid())

            with self.assertRaises(Conflict):
                read_managed(link / "file")

            with self.assertRaises(Conflict):
                remove_managed_file(link / "file")

            self.assertEqual(sentinel.read_text(), "unchanged")

    def test_atomic_write_and_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "test.txt"
            written = atomic_write(
                target, "hello", mode=0o644, uid=os.getuid(), gid=os.getgid()
            )
            self.assertTrue(written)
            self.assertEqual(read_managed(target), "hello")

            # Same content returns False
            written_again = atomic_write(
                target, "hello", mode=0o644, uid=os.getuid(), gid=os.getgid()
            )
            self.assertFalse(written_again)

            # Removal
            removed = remove_managed_file(target)
            self.assertTrue(removed)
            self.assertEqual(read_managed(target), "")

            # Non-existent parent directory returns False / empty string
            non_existent = Path(directory) / "no_such_dir" / "file.txt"
            self.assertFalse(remove_managed_file(non_existent))
            self.assertEqual(read_managed(non_existent), "")


if __name__ == "__main__":
    unittest.main()
