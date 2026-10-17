"""NetworkManager Wi-Fi AP hotspot management primitives."""

from __future__ import annotations
import ipaddress
import json
import re
from typing import Any, Callable

from .exceptions import Conflict
from .files import Files
from .firewall import FirewallBackend
from .native import Native


def validate_hotspot_address(address_str: str) -> ipaddress.IPv4Interface:
    """Validate that the address is a usable IPv4 host address with subnet <= 30."""
    try:
        address = ipaddress.IPv4Interface(address_str)
        if address.network.prefixlen > 30 or address.ip in (
            address.network.network_address,
            address.network.broadcast_address,
        ):
            raise ValueError
        return address
    except ValueError:
        raise Conflict(
            "hotspot requires a usable IPv4 host address and subnet"
        ) from None


def hotspot_properties(desired: dict[str, Any]) -> dict[str, str]:
    """Build the dictionary of NetworkManager connection properties for the AP."""
    return {
        "connection.interface-name": desired["interface"],
        "connection.autoconnect": "yes" if desired.get("autoconnect") else "no",
        "802-11-wireless.ssid": desired["ssid"],
        "802-11-wireless.band": desired["band"],
        "802-11-wireless.channel": str(desired["channel"]),
        "ipv4.method": "shared",
        "ipv4.addresses": desired["address"],
        "ipv4.shared-dhcp-range": "",
        "ipv4.shared-dhcp-lease-time": "0",
        "ipv6.method": desired.get("ipv6", "shared"),
    }


def is_hotspot_active(
    interface: str,
    ssid: str,
    channel: int,
    address: str,
    runner: Native | None = None,
) -> bool:
    """Verify that the AP is active on the radio (iw) and has the expected IP (iproute2)."""
    r = runner or Native()
    wireless = r.run("iw", "dev", interface, "info").stdout
    c_match = re.search(r"^\s*channel (\d+) ", wireless, re.M)
    s_match = re.search(r"^\s*ssid (.*)$", wireless, re.M)
    if not c_match or int(c_match[1]) != channel or not s_match or s_match[1] != ssid:
        return False

    data = json.loads(
        r.run("ip", "-j", "-4", "address", "show", "dev", interface).stdout
    )
    addr = ipaddress.IPv4Interface(address)
    return any(
        a.get("local") == str(addr.ip) and a.get("prefixlen") == addr.network.prefixlen
        for link in data
        for a in link.get("addr_info", [])
    )


def find_hotspot_connection_uuid(
    connection_name: str, runner: Native | None = None
) -> str:
    """Locate the single prepared NetworkManager connection UUID by name."""
    r = runner or Native()
    lines = r.run(
        "nmcli", "--escape", "no", "-t", "-f", "UUID,NAME", "connection", "show"
    ).stdout.splitlines()
    matches = [
        line.split(":", 1)[0]
        for line in lines
        if ":" in line and line.split(":", 1)[1] == connection_name
    ]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-fA-F-]{36}", matches[0]):
        raise Conflict(
            "prepare exactly one named hotspot connection and its credentials in NetworkManager before deployment"
        )
    return matches[0]


def check_hotspot_prerequisites(
    desired: dict[str, Any],
    runner: Native | None = None,
    ready_unit_fn: Callable[[str], Any] | None = None,
) -> str:
    """Verify interfaces, prepared connection properties, and security before convergence."""
    r = runner or Native()
    validate_hotspot_address(desired["address"])
    if desired["interface"] == desired["uplink"]:
        raise Conflict("hotspot and uplink interfaces must differ")

    if ready_unit_fn:
        ready_unit_fn("NetworkManager.service")

    links = json.loads(r.run("ip", "-j", "link", "show").stdout)
    if not isinstance(links, list) or any(
        not isinstance(link, dict) or not isinstance(link.get("ifname"), str)
        for link in links
    ):
        raise Conflict("invalid native link inventory")

    available = {link["ifname"] for link in links}
    for role, name in (
        ("wireless", desired["interface"]),
        ("uplink", desired["uplink"]),
    ):
        if name not in available:
            raise Conflict(f"declared hotspot {role} interface is missing: {name}")

    dev_res = r.run(
        "nmcli",
        "-g",
        "GENERAL.CON-UUID",
        "device",
        "show",
        desired["interface"],
    )

    uuid = find_hotspot_connection_uuid(desired["connection"], runner=r)

    def read_field(field: str) -> str:
        return r.run(
            "nmcli",
            "--escape",
            "no",
            "-g",
            field,
            "connection",
            "show",
            "uuid",
            uuid,
        ).stdout.strip()

    if (
        read_field("connection.type") != "802-11-wireless"
        or read_field("802-11-wireless.mode") != "ap"
    ):
        raise Conflict("prepared hotspot must be a Wi-Fi AP connection")
    if read_field("802-11-wireless-security.key-mgmt") not in ("wpa-psk", "sae"):
        raise Conflict("prepared hotspot must use WPA personal security")
    if read_field("connection.interface-name") not in ("", desired["interface"]):
        raise Conflict("prepared hotspot belongs to a different interface")

    device_uuid = dev_res.stdout.strip()
    if device_uuid not in ("", "--", uuid):
        raise Conflict("hotspot interface is in use by another connection")

    return uuid


class HotspotManager:
    """Manages NetworkManager Wi-Fi AP lifecycle and convergence."""

    def __init__(
        self,
        desired: dict[str, Any],
        runner: Native | None = None,
        files: Files | None = None,
        firewall_backend: FirewallBackend | None = None,
    ):
        self.desired = desired
        self.runner = runner or Native()
        self.files = files or Files()
        self.firewall_backend = firewall_backend
        self.uuid: str | None = None

    def properties(self) -> dict[str, str]:
        return hotspot_properties(self.desired)

    def is_selected(self) -> bool:
        if not self.uuid:
            return False
        return (
            self.uuid
            in self.runner.run(
                "nmcli", "-g", "UUID", "connection", "show", "--active"
            ).stdout.splitlines()
        )

    def is_active(self) -> bool:
        if not self.is_selected():
            return False
        return is_hotspot_active(
            interface=self.desired["interface"],
            ssid=self.desired["ssid"],
            channel=self.desired["channel"],
            address=self.desired["address"],
            runner=self.runner,
        )

    def read_property(self, field: str) -> str:
        return self.runner.run(
            "nmcli",
            "--escape",
            "no",
            "-g",
            field,
            "connection",
            "show",
            "uuid",
            self.uuid or "",
        ).stdout.strip()

    def preflight(self, ready_unit_fn: Callable[[str], Any] | None = None) -> bool:
        self.uuid = check_hotspot_prerequisites(
            self.desired, runner=self.runner, ready_unit_fn=ready_unit_fn
        )
        return True

    def converge(
        self,
        on_action: Callable[[], None] | None = None,
        ready_unit_fn: Callable[[str], Any] | None = None,
    ) -> None:
        self.preflight(ready_unit_fn=ready_unit_fn)
        assert self.uuid is not None

        changes: list[str] = []
        for field, expected in self.properties().items():
            actual = self.read_property(field)
            if field == "ipv4.shared-dhcp-lease-time":
                actual = actual.split()[0]
            if actual == "--":
                actual = ""
            if actual != expected:
                changes.extend((field, expected))

        pending = self.files.pending("hotspot")
        selected = self.is_selected()
        active = self.is_active()
        marker_file = self.files.marker("hotspot")
        marker_content = (
            self.files.read(marker_file)
            if pending and self.files.path(marker_file).exists()
            else ""
        )
        activate = (
            selected
            or self.desired.get("autoconnect", False)
            or (pending and marker_content == "activate\n")
        )

        if changes:
            self.files.mark("hotspot", "activate\n" if activate else "pending\n")
            self.runner.run(
                "nmcli", "connection", "modify", "uuid", self.uuid, *changes
            )
            if on_action:
                on_action()

        if activate and (changes or pending or not active):
            self.files.mark("hotspot", "activate\n")
            self.runner.run(
                "nmcli",
                "connection",
                "up",
                "uuid",
                self.uuid,
                "ifname",
                self.desired["interface"],
            )
            if on_action:
                on_action()
            if not self.is_active():
                raise Conflict(
                    "hotspot activation did not converge; pending action retained"
                )

        if self.firewall_backend:
            self.converge_firewall(on_action=on_action)

        self.files.clear("hotspot")

    def converge_firewall(self, on_action: Callable[[], None] | None = None) -> None:
        if self.firewall_backend:
            self.firewall_backend.converge_hotspot_rules(
                interface=self.desired["interface"],
                uplink=self.desired["uplink"],
                address=self.desired["address"],
                on_action=on_action,
            )


def firewall_rules(desired: dict[str, Any]) -> list[tuple[list[str], list[str]]]:
    """Return scoped UFW commands and their corresponding IPv4 kernel rules."""
    from .firewall import hotspot_firewall_rules

    return hotspot_firewall_rules(
        interface=desired["interface"],
        uplink=desired["uplink"],
        address=desired["address"],
    )


def converge_firewall(system: Any) -> None:
    desired = system.desired.get("hotspot") if hasattr(system, "desired") else None
    if desired is None:
        return
    from .firewall import UfwBackend

    backend = getattr(system, "firewall_backend", None) or UfwBackend(
        runner=getattr(system, "native", None)
        or getattr(system, "runner", None)
        or (system if hasattr(system, "run") else None),
        files=getattr(system, "files", None),
    )

    def on_action() -> None:
        if hasattr(system, "actions"):
            system.actions += 1

    backend.converge_hotspot_rules(
        interface=desired["interface"],
        uplink=desired["uplink"],
        address=desired["address"],
        on_action=on_action,
    )


class Hotspot:
    def __init__(self, system: Any, manager: HotspotManager | None = None):
        self.system = system
        self.desired = (
            system.desired.get("hotspot", {}) if hasattr(system, "desired") else {}
        )
        if manager is not None:
            self.manager = manager
        else:
            runner = (
                getattr(system, "native", None)
                or getattr(system, "runner", None)
                or (system if hasattr(system, "run") else None)
            )
            files = getattr(system, "files", None)
            self.manager = HotspotManager(
                desired=self.desired,
                runner=runner,
                files=files,
            )

    @property
    def uuid(self) -> str | None:
        return self.manager.uuid

    @uuid.setter
    def uuid(self, value: str | None) -> None:
        self.manager.uuid = value

    def read(self, field: str) -> str:
        return self.manager.read_property(field)

    def properties(self) -> dict[str, str]:
        return self.manager.properties()

    def selected(self) -> bool:
        return self.manager.is_selected()

    def active(self) -> bool:
        return self.manager.is_active()

    def preflight(self, installed: bool = False) -> bool:
        ready_unit = getattr(self.system, "ready_unit", None)
        return self.manager.preflight(ready_unit_fn=ready_unit)

    def converge(self) -> None:
        def on_action() -> None:
            if hasattr(self.system, "actions"):
                self.system.actions += 1

        ready_unit = getattr(self.system, "ready_unit", None)
        self.manager.converge(on_action=on_action, ready_unit_fn=ready_unit)
