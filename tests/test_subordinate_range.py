# pyright: standard
"""Exercise native allocation-file validation through the public writer."""

from __future__ import annotations

import locale
import os
import tempfile
import unittest
from pathlib import Path
from typing import cast

from nix_adapter import Conflict, ensure_subordinate_range


class SubordinateRangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "subuid"
        self.desired = {"start": 100000, "count": 65536}

    def snapshot(self):
        info = self.path.stat()
        return self.path.read_bytes(), info.st_ino, info.st_mtime_ns, info.st_mode

    def test_healthy_alias_comments_and_repeat_metadata_are_preserved(self):
        for ending in ("\n", "\r\n"):
            with self.subTest(comment_ending=ending):
                content = f"# public allocation{ending}\n1001:100000:65536\n".encode()
                self.path.write_bytes(content)
                self.path.chmod(0o644)
                before = self.snapshot()
                ensure_subordinate_range(
                    self.path, "alice", self.desired, aliases=("1001",)
                )
                self.assertEqual(self.path.read_bytes(), content)
                self.assertEqual(self.snapshot(), before)
                ensure_subordinate_range(
                    self.path, "alice", self.desired, aliases=("1001",)
                )
                self.assertEqual(self.snapshot(), before)

    def test_malformed_native_files_are_rejected_without_writes(self):
        for content in (
            "alice:100000:65536\nalice:300000:65536\n",
            "bob:120000:65536\n",
            "bob:200000:65536\ncarol:220000:65536\n",
            "alice:100000:65536\nalice:100000:65536\n",
            "broken\n",
            b"\xffalice:100000:65536\n",
            b"# synthetic comment\x00\nalice:100000:65536\n",
            "+alice:100000:65536\n",
            "-alice:100000:65536\n",
            "alice:0100000:65536\n",
            "alice:" + "0" * 1024 + "100000:65536\n",
            "alice:100000:65536\n" + "b" * 1011 + ":200000:65536\n",
            " alice:100000:65536\n",
            "alice:100000:65536 \n",
            "alice:100000:65536\vbob:200000:65536\n",
            "alice:100000:65536\fbob:200000:65536\n",
            "alice:100000:65536\u0085bob:200000:65536\n",
            "alice:100000:65536\r\n",
            ":200000:65536\n",
            "bob:0:65536\n",
            "bob:200000:0\n",
            "bob:4294967294:2\n",
            "bob:٢٠٠٠٠٠:65536\n",
        ):
            with self.subTest(content=content):
                self.path.write_bytes(
                    content.encode("utf-8") if isinstance(content, str) else content
                )
                before = self.snapshot()
                with self.assertRaises(Conflict):
                    ensure_subordinate_range(self.path, "alice", self.desired)
                self.assertEqual(self.snapshot(), before)

    def test_disjoint_allocations_for_unmanaged_accounts_remain_valid(self):
        existing = "bob:200000:65536\nbob:300000:65536\n"
        self.path.write_text(existing)
        ensure_subordinate_range(self.path, "alice", self.desired)
        self.assertEqual(self.path.read_text(), existing + "alice:100000:65536\n")

    def test_invalid_desired_ranges_and_injected_accounts_never_write(self):
        for user, desired in (
            ("alice\nbob", self.desired),
            ("alice:123", self.desired),
            ("#alice", self.desired),
            ("+alice", self.desired),
            ("-alice", self.desired),
            ("a" * 1011, self.desired),
            ("é" * 510, self.desired),
            ("", self.desired),
            ("alice", {"start": True, "count": 65536}),
            ("alice", {"start": -1, "count": 65536}),
            ("alice", {"start": 100000, "count": 0}),
            ("alice", {"start": 4294967294, "count": 2}),
        ):
            with self.subTest(user=user, desired=desired), self.assertRaises(Conflict):
                ensure_subordinate_range(self.path, user, desired)
            self.assertFalse(self.path.exists())

    def test_named_and_numeric_aliases_cannot_allocate_twice(self):
        self.path.write_text("1001:100000:65536\nalice:300000:65536\n")
        before = self.snapshot()
        with self.assertRaises(Conflict):
            ensure_subordinate_range(
                self.path, "alice", self.desired, aliases=("1001",)
            )
        self.assertEqual(self.snapshot(), before)

    def test_alias_update_preserves_the_owner_without_adding_another_allocation(self):
        self.path.write_text("1001:100000:32768\n")
        ensure_subordinate_range(self.path, "alice", self.desired, aliases=("1001",))
        self.assertEqual(self.path.read_text(), "1001:100000:65536\n")

    def test_alias_updates_cannot_exceed_the_native_record_limit(self):
        alias = "a" * 1010
        self.path.write_text(f"{alias}:100000:65536\n")
        before = self.snapshot()
        with self.assertRaises(Conflict):
            ensure_subordinate_range(
                self.path, "alice", {"start": 100000, "count": 100000}, aliases=(alias,)
            )
        self.assertEqual(self.snapshot(), before)

    def test_invalid_owner_aliases_never_create_files(self):
        for aliases in (("#1001",), ("+1001",), ("1001\n",), "1001", (1001,), ["1001"]):
            with self.subTest(aliases=aliases), self.assertRaises(Conflict):
                ensure_subordinate_range(
                    self.path,
                    "alice",
                    self.desired,
                    aliases=cast("tuple[str, ...]", aliases),
                )
            self.assertFalse(self.path.exists())

    def test_native_record_boundary_is_counted_in_bytes_before_lf(self):
        for user in ("a" * 1010, "é" * 505):
            with self.subTest(owner_bytes=len(user.encode("utf-8"))):
                self.path.unlink(missing_ok=True)
                ensure_subordinate_range(self.path, user, self.desired)
                self.assertEqual(len(self.path.read_bytes()), 1024)

    def test_utf8_writer_is_independent_of_the_process_locale(self):
        previous = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "C")
            ensure_subordinate_range(self.path, "é", self.desired)
            self.assertEqual(self.path.read_bytes(), b"\xc3\xa9:100000:65536\n")
            before = self.snapshot()
            ensure_subordinate_range(self.path, "é", self.desired)
            self.assertEqual(self.snapshot(), before)
            ensure_subordinate_range(self.path, "é", {"start": 100000, "count": 32768})
            self.assertEqual(self.path.read_bytes(), b"\xc3\xa9:100000:32768\n")
        finally:
            locale.setlocale(locale.LC_CTYPE, previous)

    def test_symlinks_hardlinks_and_nonregular_files_are_rejected(self):
        target = self.root / "target"
        target.write_text("alice:100000:65536\n")
        for kind in ("symlink", "dangling", "hardlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                if kind in ("symlink", "dangling"):
                    self.path.symlink_to(
                        target if kind == "symlink" else self.root / "absent"
                    )
                elif kind == "hardlink":
                    os.link(target, self.path)
                elif kind == "directory":
                    self.path.mkdir()
                else:
                    os.mkfifo(self.path)
                with self.assertRaises(Conflict):
                    ensure_subordinate_range(self.path, "alice", self.desired)
                self.assertEqual(target.read_text(), "alice:100000:65536\n")
                if kind == "directory":
                    self.path.rmdir()
                else:
                    self.path.unlink()

    def test_symlinked_ancestors_are_rejected(self):
        actual = self.root / "actual"
        actual.mkdir()
        (self.root / "alias").symlink_to(actual, target_is_directory=True)
        with self.assertRaises(Conflict):
            ensure_subordinate_range(self.root / "alias/subuid", "alice", self.desired)
        self.assertEqual(list(actual.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
