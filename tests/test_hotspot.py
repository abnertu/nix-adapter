"""Unit tests for nix_adapter.hotspot."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from nix_adapter.exceptions import Conflict
from nix_adapter.hotspot import (
    HotspotManager,
    check_hotspot_prerequisites,
    find_hotspot_connection_uuid,
    hotspot_properties,
    is_hotspot_active,
    validate_hotspot_address,
)


class HotspotTests(unittest.TestCase):
    def test_validate_hotspot_address(self):
        addr = validate_hotspot_address("192.0.2.1/24")
        self.assertEqual(str(addr.ip), "192.0.2.1")
        self.assertEqual(addr.network.prefixlen, 24)

        # Subnet > 30 should fail
        with self.assertRaises(Conflict):
            validate_hotspot_address("192.0.2.1/31")

        # Network address should fail
        with self.assertRaises(Conflict):
            validate_hotspot_address("192.0.2.0/24")

        # Broadcast address should fail
        with self.assertRaises(Conflict):
            validate_hotspot_address("192.0.2.255/24")

    def test_hotspot_properties(self):
        desired = {
            "interface": "wlan0",
            "ssid": "TestAP",
            "band": "a",
            "channel": 36,
            "address": "192.0.2.1/24",
            "autoconnect": True,
            "ipv6": "shared",
        }
        props = hotspot_properties(desired)
        self.assertEqual(props["connection.interface-name"], "wlan0")
        self.assertEqual(props["802-11-wireless.ssid"], "TestAP")
        self.assertEqual(props["802-11-wireless.channel"], "36")
        self.assertEqual(props["ipv4.method"], "shared")
        self.assertEqual(props["connection.autoconnect"], "yes")

    def test_find_hotspot_connection_uuid(self):
        mock_runner = MagicMock()
        uuid = "11111111-1111-1111-1111-111111111111"
        mock_runner.run.return_value.stdout = f"{uuid}:MyAP\n"
        found = find_hotspot_connection_uuid("MyAP", runner=mock_runner)
        self.assertEqual(found, uuid)

        # Missing connection
        mock_runner.run.return_value.stdout = (
            "22222222-2222-2222-2222-222222222222:Other\n"
        )
        with self.assertRaises(Conflict):
            find_hotspot_connection_uuid("MyAP", runner=mock_runner)

        # Duplicate connection
        mock_runner.run.return_value.stdout = f"{uuid}:MyAP\n{uuid}:MyAP\n"
        with self.assertRaises(Conflict):
            find_hotspot_connection_uuid("MyAP", runner=mock_runner)

    def test_is_hotspot_active(self):
        mock_runner = MagicMock()
        mock_runner.run.side_effect = [
            # iw
            SimpleNamespace(stdout="\tssid MyAP\n\tchannel 36 (5180 MHz)\n"),
            # ip
            SimpleNamespace(
                stdout='[{"addr_info":[{"local":"192.0.2.1","prefixlen":24}]}]'
            ),
        ]
        self.assertTrue(
            is_hotspot_active("wlan0", "MyAP", 36, "192.0.2.1/24", runner=mock_runner)
        )

        mock_runner.run.side_effect = [
            SimpleNamespace(stdout="\tssid Other\n\tchannel 36 (5180 MHz)\n"),
        ]
        self.assertFalse(
            is_hotspot_active("wlan0", "MyAP", 36, "192.0.2.1/24", runner=mock_runner)
        )

    def test_check_hotspot_prerequisites(self):
        uuid = "11111111-1111-1111-1111-111111111111"
        desired = {
            "connection": "MyAP",
            "interface": "wlan0",
            "uplink": "eth0",
            "ssid": "MyAP",
            "band": "a",
            "channel": 36,
            "address": "192.0.2.1/24",
            "autoconnect": True,
            "ipv6": "shared",
        }

        def fake_run(*args, **kwargs):
            if args[:4] == ("ip", "-j", "link", "show"):
                return SimpleNamespace(
                    stdout=json.dumps([{"ifname": "wlan0"}, {"ifname": "eth0"}])
                )
            if "UUID,NAME" in args:
                return SimpleNamespace(stdout=f"{uuid}:MyAP\n")
            if "GENERAL.CON-UUID" in args:
                return SimpleNamespace(stdout=uuid)
            if "-g" in args:
                prop = args[args.index("-g") + 1]
                values = {
                    "connection.type": "802-11-wireless",
                    "802-11-wireless.mode": "ap",
                    "802-11-wireless-security.key-mgmt": "wpa-psk",
                    "connection.interface-name": "wlan0",
                }
                return SimpleNamespace(stdout=values.get(prop, ""))
            return SimpleNamespace(stdout="", returncode=0)

        mock_runner = MagicMock()
        mock_runner.run.side_effect = fake_run

        found_uuid = check_hotspot_prerequisites(desired, runner=mock_runner)
        self.assertEqual(found_uuid, uuid)

    def test_hotspot_manager_preflight_and_converge(self):
        uuid = "11111111-1111-1111-1111-111111111111"
        desired = {
            "connection": "MyAP",
            "interface": "wlan0",
            "uplink": "eth0",
            "ssid": "MyAP",
            "band": "a",
            "channel": 36,
            "address": "192.0.2.1/24",
            "autoconnect": True,
            "ipv6": "shared",
        }

        def fake_run(*args, **kwargs):
            if args[:4] == ("ip", "-j", "link", "show"):
                return SimpleNamespace(
                    stdout=json.dumps([{"ifname": "wlan0"}, {"ifname": "eth0"}])
                )
            if "UUID,NAME" in args:
                return SimpleNamespace(stdout=f"{uuid}:MyAP\n")
            if "GENERAL.CON-UUID" in args:
                return SimpleNamespace(stdout=uuid)
            if "--active" in args:
                return SimpleNamespace(stdout=uuid)
            if args[0] == "iw":
                return SimpleNamespace(stdout="\tssid MyAP\n\tchannel 36 (5180 MHz)\n")
            if args[:4] == ("ip", "-j", "-4", "address"):
                return SimpleNamespace(
                    stdout='[{"addr_info":[{"local":"192.0.2.1","prefixlen":24}]}]'
                )
            if "-g" in args:
                prop = args[args.index("-g") + 1]
                values = {
                    "connection.type": "802-11-wireless",
                    "802-11-wireless.mode": "ap",
                    "802-11-wireless-security.key-mgmt": "wpa-psk",
                    "connection.interface-name": "wlan0",
                    "connection.autoconnect": "yes",
                    "802-11-wireless.ssid": "MyAP",
                    "802-11-wireless.band": "a",
                    "802-11-wireless.channel": "36",
                    "ipv4.method": "shared",
                    "ipv4.addresses": "192.0.2.1/24",
                    "ipv4.shared-dhcp-range": "",
                    "ipv4.shared-dhcp-lease-time": "0",
                    "ipv6.method": "shared",
                }
                return SimpleNamespace(stdout=values.get(prop, ""))
            return SimpleNamespace(stdout="", returncode=0)

        mock_runner = MagicMock()
        mock_runner.run.side_effect = fake_run

        manager = HotspotManager(desired, runner=mock_runner)
        self.assertTrue(manager.preflight())
        manager.converge()

    def test_firewall_rules(self):
        from nix_adapter.hotspot import firewall_rules

        desired = {
            "interface": "wlan0",
            "uplink": "eth0",
            "address": "192.0.2.1/24",
        }
        rules = firewall_rules(desired)
        self.assertGreater(len(rules), 0)

    def test_hotspot_adapter(self):
        from nix_adapter.hotspot import Hotspot

        mock_system = MagicMock()
        mock_system.desired = {
            "hotspot": {
                "interface": "wlan0",
                "ssid": "TestAP",
                "band": "a",
                "channel": 36,
                "address": "192.0.2.1/24",
                "autoconnect": True,
                "ipv6": "shared",
            }
        }
        hs = Hotspot(mock_system)
        self.assertIsNotNone(hs.manager)


if __name__ == "__main__":
    unittest.main()
