"""BaseAdapter unit convergence against a stateful fake systemd."""

import os
import tempfile
import unittest

from nix_adapter import BaseAdapter, Change, Conflict, FakeNative, FakeProcess, Files


class FakeSystemd(FakeNative):
    def __init__(self):
        super().__init__()
        self.state = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "UnitFileState": "disabled",
        }
        self.stays_inactive = False

    def run(self, *argv, check=True, **kwargs):
        self.calls.append(argv)
        verb = argv[1]
        if verb == "show":
            return FakeProcess("\n".join(f"{k}={v}" for k, v in self.state.items()))
        if verb == "enable":
            self.state["UnitFileState"] = "enabled"
        elif verb in ("start", "restart") and not self.stays_inactive:
            self.state["ActiveState"] = "active"
        return FakeProcess()


class ConvergeUnitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.native = FakeSystemd()
        self.adapter = BaseAdapter({}, root=temp.name, runner=self.native)
        self.marks = []

    def converge(self, **kwargs):
        self.adapter.converge_unit(
            "demo.service", before_change=lambda: self.marks.append(1), **kwargs
        )

    def test_enables_and_starts_then_repeat_is_quiet(self):
        self.converge()
        self.assertEqual((self.adapter.actions, len(self.marks)), (2, 2))
        self.converge()
        self.assertEqual((self.adapter.actions, len(self.marks)), (2, 2))

    def test_restart_is_explicit(self):
        self.converge()
        self.converge(restart=True)
        self.assertEqual(self.native.calls[-2][1], "restart")

    def test_unit_that_stays_inactive_fails_with_its_name(self):
        self.native.stays_inactive = True
        with self.assertRaisesRegex(
            Conflict, "demo.service did not become enabled and active"
        ):
            self.converge()

    def test_missing_unit_is_rejected_before_changes(self):
        self.native.state["LoadState"] = "not-found"
        with self.assertRaises(Conflict):
            self.converge()
        self.assertEqual(self.marks, [])


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.native = FakeSystemd()
        files = Files(temp.name, (os.getuid(), os.getgid()))
        self.adapter = BaseAdapter({}, root=temp.name, runner=self.native, files=files)

    def test_plan_unit_matches_what_converge_unit_would_do(self):
        self.assertEqual(
            self.adapter.plan_unit("fixture.service"),
            [
                Change("~", "enable fixture.service"),
                Change("~", "start fixture.service"),
            ],
        )
        self.adapter.converge_unit("fixture.service")
        self.native.calls.clear()
        self.assertEqual(
            self.adapter.plan_unit("fixture.service"), [Change("=", "fixture.service")]
        )
        self.assertEqual([call[1] for call in self.native.calls], ["show"])

    def test_plan_file_reports_missing_differing_and_matching(self):
        files = self.adapter.files
        self.assertEqual(self.adapter.plan_file("/etc/x.conf", "a\n").kind, "+")
        files.write("/etc/x.conf", "old\n")
        self.assertEqual(
            self.adapter.plan_file("/etc/x.conf", "a\n"),
            Change("~", "/etc/x.conf (content differs)"),
        )
        files.write("/etc/x.conf", "a\n")
        self.assertEqual(self.adapter.plan_file("/etc/x.conf", "a\n").kind, "=")
        files.path("/etc/x.conf").chmod(0o600)
        self.assertEqual(
            self.adapter.plan_file("/etc/x.conf", "a\n"),
            Change("~", "/etc/x.conf (mode or owner differs)"),
        )


if __name__ == "__main__":
    unittest.main()
