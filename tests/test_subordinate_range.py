# pyright: standard
"""Exercise native allocation-file validation through the public writer."""

from __future__ import annotations

import locale
import os
import tempfile
import unittest
from pathlib import Path
from typing import cast

from nix_adapter import Conflict, ensure_subordinate_range, inspect_subordinate_range


class _SubordinateFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "subuid"
        self.desired = {"start": 100000, "count": 65536}

    def snapshot(self):
        info = self.path.stat()
        return self.path.read_bytes(), info.st_ino, info.st_mtime_ns, info.st_mode


class SubordinateRangeTests(_SubordinateFixture):
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
            for operation in (ensure_subordinate_range, inspect_subordinate_range):
                with self.subTest(content=content, operation=operation.__name__):
                    self.path.write_bytes(
                        content.encode("utf-8") if isinstance(content, str) else content
                    )
                    before = self.snapshot()
                    with self.assertRaises(Conflict):
                        operation(self.path, "alice", self.desired)
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


class SubordinateInspectionTests(_SubordinateFixture):
    def test_missing_file_and_parent_are_not_created(self):
        missing = self.root / "missing" / "subgid"
        self.assertIs(inspect_subordinate_range(missing, "alice", self.desired), False)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_state_is_reported_without_changing_files(self):
        for content, expected in (
            ("# public allocation\n\nalice:100000:65536\n", True),
            ("alice:100000:32768\n", False),
            ("bob:200000:65536\n", False),
        ):
            with self.subTest(content=content):
                self.path.write_text(content)
                before = self.snapshot()
                self.assertIs(
                    inspect_subordinate_range(self.path, "alice", self.desired),
                    expected,
                )
                self.assertEqual(self.snapshot(), before)

    def test_same_range_fields_as_writer_ignore_extra_caller_metadata(self):
        desired = self.desired | {"extra": 1}
        ensure_subordinate_range(self.path, "alice", desired)
        before = self.snapshot()
        self.assertIs(inspect_subordinate_range(self.path, "alice", desired), True)
        self.assertEqual(self.snapshot(), before)

    def test_named_and_numeric_owner_identity_rejects_duplicate_allocations(self):
        self.path.write_text("1001:100000:65536\n")
        before = self.snapshot()
        self.assertIs(
            inspect_subordinate_range(
                self.path, "alice", self.desired, aliases=("1001",)
            ),
            True,
        )
        self.assertEqual(self.snapshot(), before)
        self.path.write_text(self.path.read_text() + "alice:300000:65536\n")
        before = self.snapshot()
        with self.assertRaises(Conflict):
            inspect_subordinate_range(
                self.path, "alice", self.desired, aliases=("1001",)
            )
        self.assertEqual(self.snapshot(), before)

    def test_utf8_inspection_is_read_only_under_an_ascii_locale(self):
        self.path.write_bytes(b"# comment\r\n\xc3\xa9:100000:65536\n")
        before = self.snapshot()
        previous = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "C")
            self.assertIs(inspect_subordinate_range(self.path, "é", self.desired), True)
            self.assertEqual(self.snapshot(), before)
        finally:
            locale.setlocale(locale.LC_CTYPE, previous)

    def test_unsafe_paths_are_not_followed_or_created(self):
        actual = self.root / "actual"
        actual.mkdir()
        (self.root / "alias").symlink_to(actual, target_is_directory=True)
        with self.assertRaises(Conflict):
            inspect_subordinate_range(self.root / "alias/subuid", "alice", self.desired)
        self.assertEqual(list(actual.iterdir()), [])
        target = self.root / "target"
        target.write_text("alice:100000:65536\n")
        self.path.symlink_to(target)
        before = target.read_bytes()
        with self.assertRaises(Conflict):
            inspect_subordinate_range(self.path, "alice", self.desired)
        self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
