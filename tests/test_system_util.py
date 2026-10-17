"""Unit tests for nix_adapter.system."""

import tempfile
import unittest
from unittest.mock import MagicMock

from nix_adapter.exceptions import Conflict
from nix_adapter.system import (
    check_discard_support,
    ensure_subordinate_range,
    get_hostname,
    get_timezone,
    is_ntp_synchronized,
    ranges_overlap,
    set_hostname,
    set_timezone,
)


class SystemUtilTests(unittest.TestCase):
    def test_ranges_overlap(self):
        r1 = {"start": 100000, "count": 65536}
        r2 = {"start": 200000, "count": 65536}
        self.assertFalse(ranges_overlap(r1, r2))
        self.assertFalse(ranges_overlap(r2, r1))

        # Overlapping ranges
        r3 = {"start": 150000, "count": 20000}
        self.assertTrue(ranges_overlap(r1, r3))
        self.assertTrue(ranges_overlap(r3, r1))

    def test_ensure_subordinate_range(self):
        with tempfile.TemporaryDirectory() as d:
            path = tempfile.mktemp(dir=d)
            # Create fresh
            ensure_subordinate_range(path, "alice", {"start": 100000, "count": 65536})
            with open(path) as f:
                content = f.read()
            self.assertEqual(content, "alice:100000:65536\n")

            # Add non-overlapping user
            ensure_subordinate_range(path, "bob", {"start": 200000, "count": 65536})
            with open(path) as f:
                lines = f.read().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], "alice:100000:65536")
            self.assertEqual(lines[1], "bob:200000:65536")

            # Overlapping allocation fails
            with self.assertRaises(Conflict):
                ensure_subordinate_range(
                    path, "charlie", {"start": 150000, "count": 10000}
                )

            # Update existing user
            ensure_subordinate_range(path, "alice", {"start": 100000, "count": 32768})
            with open(path) as f:
                lines = f.read().splitlines()
            self.assertEqual(lines[0], "alice:100000:32768")

    def test_get_and_set_timezone(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.stdout = "Timezone=UTC\nLocalRTC=no\n"
        with tempfile.TemporaryDirectory() as d:
            zone = get_timezone(runner=mock_runner, root=d)
            self.assertEqual(zone, "UTC")

        set_timezone("Asia/Taipei", runner=mock_runner)
        mock_runner.run.assert_called_with("timedatectl", "set-timezone", "Asia/Taipei")

    def test_get_timezone_local_rtc_conflict(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.stdout = "Timezone=UTC\nLocalRTC=yes\n"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Conflict):
                get_timezone(runner=mock_runner, root=d)

    def test_get_and_set_hostname(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.stdout = "myhost\n"
        self.assertEqual(get_hostname(runner=mock_runner), "myhost")

        set_hostname("newhost", runner=mock_runner)
        mock_runner.run.assert_called_with(
            "hostnamectl", "--static", "set-hostname", "newhost"
        )

    def test_is_ntp_synchronized(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.stdout = "yes\n"
        self.assertTrue(is_ntp_synchronized(runner=mock_runner))

        mock_runner.run.return_value.stdout = "no\n"
        self.assertFalse(is_ntp_synchronized(runner=mock_runner))

    def test_check_discard_support(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.stdout = '{"blockdevices":[{"name":"nvme0n1","type":"disk","disc-max":2147483648,"mountpoints":["/"]}]}'
        self.assertTrue(check_discard_support(runner=mock_runner))

        mock_runner.run.return_value.stdout = '{"blockdevices":[{"name":"sda","type":"disk","disc-max":0,"mountpoints":["/"]}]}'
        with self.assertRaises(Conflict):
            check_discard_support(runner=mock_runner)


if __name__ == "__main__":
    unittest.main()
