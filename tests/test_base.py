"""BaseAdapter unit convergence against a stateful fake systemd."""

import tempfile
import unittest

from nix_adapter import BaseAdapter, Conflict, FakeNative, FakeProcess


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


if __name__ == "__main__":
    unittest.main()
