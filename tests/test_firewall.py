"""Unit tests for nix_adapter.firewall."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from nix_adapter.exceptions import Conflict
from nix_adapter.files import Files
from nix_adapter.firewall import (
    FirewalldBackend,
    UfwBackend,
    canonical_rule,
    check_kernel_policy,
    check_kernel_rule,
    format_port_range,
    hotspot_firewall_rules,
    parse_ufw_status,
)


class FirewallTests(unittest.TestCase):
    def test_format_port_range(self):
        self.assertEqual(format_port_range(80), "80")
        self.assertEqual(format_port_range(80, 80), "80")
        self.assertEqual(format_port_range(27031, 27036), "27031:27036")

    def test_parse_ufw_status_inactive(self):
        res = parse_ufw_status("Status: inactive\n")
        self.assertFalse(res["active"])
        self.assertEqual(res["rules"], set())

    def test_parse_ufw_status_active(self):
        sample = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80:443/tcp (v6)            ALLOW IN    Anywhere (v6)
"""
        res = parse_ufw_status(sample)
        self.assertTrue(res["active"])
        self.assertEqual(res["logging"], "low")
        self.assertEqual(res["profiles"], "skip")
        self.assertEqual(res["policy"], ("deny", "allow", "disabled"))
        self.assertIn(("22", "tcp", False), res["rules"])
        self.assertIn(("80:443", "tcp", True), res["rules"])

    def test_parse_ufw_status_restrictive_rule_conflict(self):
        sample = """Status: active
Logging: off
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     DENY IN     Anywhere
"""
        with self.assertRaises(Conflict):
            parse_ufw_status(sample)

    def test_check_kernel_rule(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.returncode = 0
        self.assertTrue(
            check_kernel_rule(
                "ufw-user-input", "tcp", "80", v6=False, runner=mock_runner
            )
        )
        mock_runner.run.assert_called_with(
            "iptables",
            "-w",
            "5",
            "-C",
            "ufw-user-input",
            "-p",
            "tcp",
            "--dport",
            "80",
            "-j",
            "ACCEPT",
            check=False,
        )

    def test_check_kernel_policy(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value.returncode = 0
        mock_runner.run.return_value.stdout = (
            "-P INPUT DROP\n-P OUTPUT ACCEPT\n-P FORWARD DROP\n"
        )
        self.assertTrue(check_kernel_policy(runner=mock_runner))

    def test_canonical_rule_and_hotspot_rules(self):
        rules = hotspot_firewall_rules("wifi0", "eth0", "192.0.2.1/24")
        self.assertEqual(len(rules), 4)

        r0 = canonical_rule(rules[0][0])
        r0_parsed = canonical_rule(
            "ufw allow in on wifi0 to any port 67 proto udp".split()
        )
        self.assertEqual(r0, r0_parsed)

        r1 = canonical_rule(rules[1][0])
        r1_parsed = canonical_rule(
            "ufw allow in on wifi0 from 192.0.2.0/24 to 192.0.2.1/32 port 53 proto udp".split()
        )
        self.assertEqual(r1, r1_parsed)

    def test_firewalld_backend_not_implemented(self):
        backend = FirewalldBackend()
        with self.assertRaises(NotImplementedError):
            backend.snapshot()

    def test_ufw_backend_snapshot_and_preflight(self):
        mock_runner = MagicMock()
        mock_runner.unit.side_effect = lambda name: {
            "nftables.service": {
                "ActiveState": "inactive",
                "UnitFileState": "disabled",
            },
            "ufw.service": {
                "LoadState": "loaded",
                "ActiveState": "active",
                "UnitFileState": "enabled",
            },
        }.get(name, {})
        mock_runner.run.return_value.stdout = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip
"""
        with tempfile.TemporaryDirectory() as d:
            files = Files(root=d, identity=(os.getuid(), os.getgid()))
            backend = UfwBackend(runner=mock_runner, files=files)
            snap = backend.snapshot()
            self.assertTrue(snap["active"])

            # Preflight without installed
            backend.preflight(installed=False)

            # Preflight with installed checks default files
            files.write(
                "/etc/default/ufw",
                'IPV6=yes\nMANAGE_BUILTINS=no\nDEFAULT_INPUT_POLICY="DROP"\nDEFAULT_OUTPUT_POLICY="ACCEPT"\nDEFAULT_FORWARD_POLICY="DROP"\n',
            )
            backend.preflight(installed=True)

    def test_firewall_adapter(self):
        from nix_adapter.firewall import Firewall

        mock_runner = MagicMock()
        mock_runner.run.return_value.stdout = "Status: inactive\n"
        mock_system = MagicMock()
        mock_system.native = mock_runner
        mock_system.desired = {"firewall": {"rules": [], "logging": "low"}}
        mock_system.files = MagicMock()
        fw = Firewall(mock_system)
        snap = fw.snapshot()
        self.assertFalse(snap["active"])


if __name__ == "__main__":
    unittest.main()
