"""Unit tests for Systemd service manager."""

import unittest
from unittest.mock import MagicMock

from nix_adapter.exceptions import Conflict
from nix_adapter.systemd import Systemd


class SystemdTests(unittest.TestCase):
    def setUp(self):
        self.runner = MagicMock()
        self.systemd = Systemd(runner=self.runner)

    def test_show_parses_properties(self):
        res = MagicMock()
        res.stdout = "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n"
        self.runner.run.return_value = res

        props = self.systemd.show("caddy.service", ["LoadState", "ActiveState"])
        self.assertEqual(props["LoadState"], "loaded")
        self.assertEqual(props["ActiveState"], "active")
        self.assertEqual(props["UnitFileState"], "enabled")
        self.runner.run.assert_called_with(
            "systemctl", "show", "caddy.service", "--property=LoadState,ActiveState"
        )

    def test_ready_unit_raises_on_masked(self):
        res = MagicMock()
        res.stdout = "LoadState=loaded\nActiveState=inactive\nUnitFileState=masked\n"
        self.runner.run.return_value = res

        with self.assertRaises(Conflict):
            self.systemd.ready_unit("test.service")

    def test_is_active(self):
        res = MagicMock()
        res.returncode = 0
        self.runner.run.return_value = res
        self.assertTrue(self.systemd.is_active("caddy.service"))

        res.returncode = 3
        self.assertFalse(self.systemd.is_active("caddy.service"))

    def test_user_mode(self):
        user_sd = Systemd(runner=self.runner, user=True)
        user_sd.restart("user-task.service")
        self.runner.run.assert_called_with(
            "systemctl", "--user", "restart", "user-task.service"
        )


if __name__ == "__main__":
    unittest.main()
