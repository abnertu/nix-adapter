"""Firewall backend adapter pattern and UFW / netfilter primitives."""

from __future__ import annotations
from abc import ABC, abstractmethod
import ipaddress
import re
import shlex
from typing import Any, Callable, Sequence

from .exceptions import Conflict
from .files import Files, assignments
from .native import Native


def format_port_range(from_port: int | str, to_port: int | str | None = None) -> str:
    """Format a port or port range as a string like '80' or '27031:27036'."""
    return (
        str(from_port)
        if to_port is None or to_port == from_port
        else f"{from_port}:{to_port}"
    )


def parse_ufw_status(text: str) -> dict[str, Any]:
    """Parse 'ufw status verbose' output into structured policy and rules."""
    if text.strip() == "Status: inactive":
        return {"active": False, "rules": set()}
    if not text.startswith("Status: active\n"):
        raise Conflict("unrecognized UFW status")

    policy = re.search(
        r"^Default: (deny|allow|reject) \(incoming\), (deny|allow|reject) \(outgoing\), (deny|allow|reject|disabled) \(routed\)$",
        text,
        re.M,
    )
    logging = re.search(r"^Logging: (off|on \((low|medium|high|full)\))$", text, re.M)
    profiles = re.search(r"^New profiles: (skip|allow|deny|reject)$", text, re.M)
    if not policy or not logging or not profiles:
        raise Conflict("incomplete UFW status")

    rules: set[tuple[str, str, bool]] = set()
    for line in text.splitlines():
        match = re.fullmatch(
            r"(\d+(?::\d+)?)/(tcp|udp)\s*(\(v6\))?\s+ALLOW IN\s+Anywhere(?: \(v6\))?\s*(?:#.*)?",
            line,
        )
        if match:
            port, protocol, v6 = match.groups()
            rules.add((port, protocol, bool(v6)))
        elif re.search(r"\b(DENY|REJECT|LIMIT)\b", line):
            raise Conflict(
                "UFW has restrictive rules requiring explicit ownership review"
            )

    return {
        "active": True,
        "rules": rules,
        "policy": policy.groups(),
        "logging": logging[2] or "off",
        "profiles": profiles[1],
    }


def check_kernel_rule(
    chain: str,
    protocol: str,
    port: str,
    v6: bool = False,
    runner: Native | None = None,
) -> bool:
    """Check if a specific port acceptance rule is loaded in the netfilter chain."""
    r = runner or Native()
    command = "ip6tables" if v6 else "iptables"
    match = ["-m", "multiport", "--dports", port] if ":" in port else ["--dport", port]
    return (
        r.run(
            command,
            "-w",
            "5",
            "-C",
            chain,
            "-p",
            protocol,
            *match,
            "-j",
            "ACCEPT",
            check=False,
        ).returncode
        == 0
    )


def check_kernel_policy(runner: Native | None = None) -> bool:
    """Verify that default kernel policies for INPUT, OUTPUT, FORWARD match UFW defaults."""
    r = runner or Native()
    for command in ("iptables", "ip6tables"):
        for chain, policy in (
            ("INPUT", "DROP"),
            ("OUTPUT", "ACCEPT"),
            ("FORWARD", "DROP"),
        ):
            result = r.run(command, "-w", "5", "-S", chain, check=False)
            if (
                result.returncode
                or f"-P {chain} {policy}" not in result.stdout.splitlines()
            ):
                return False
    return True


def canonical_rule(
    words: Sequence[str],
) -> tuple[tuple[str, Any], ...] | None:
    """Normalize UFW's show-added syntax; unrelated rule forms remain unowned."""
    w = list(words)
    if w[:1] == ["ufw"]:
        w.pop(0)
    routed = w[:1] == ["route"]
    if routed:
        w.pop(0)
    if w[:1] != ["allow"]:
        return None
    w.pop(0)
    result: dict[str, Any] = {"routed": routed, "from": "0.0.0.0/0", "to": "0.0.0.0/0"}
    try:
        while w:
            key = w.pop(0)
            if key in ("in", "out"):
                if w.pop(0) != "on":
                    return None
                result[key] = w.pop(0)
            elif key in ("from", "to"):
                value = w.pop(0)
                result[key] = str(
                    ipaddress.ip_network(
                        "0.0.0.0/0" if value == "any" else value, strict=False
                    )
                )
            elif key in ("port", "proto"):
                result[key] = w.pop(0)
            elif key == "comment":
                w.pop(0)
            else:
                return None
    except (IndexError, ValueError):
        return None
    return tuple(sorted(result.items()))


def hotspot_firewall_rules(
    interface: str, uplink: str, address: str
) -> list[tuple[list[str], list[str]]]:
    """Return scoped UFW commands and their corresponding IPv4 kernel rules for an AP."""
    addr = ipaddress.IPv4Interface(address)
    subnet, host = str(addr.network), str(addr.ip)
    rules: list[tuple[list[str], list[str]]] = [
        (
            ["allow", "in", "on", interface, "proto", "udp", "to", "any", "port", "67"],
            [
                "ufw-user-input",
                "-i",
                interface,
                "-p",
                "udp",
                "--dport",
                "67",
                "-j",
                "ACCEPT",
            ],
        )
    ]
    for protocol in ("udp", "tcp"):
        rules.append(
            (
                [
                    "allow",
                    "in",
                    "on",
                    interface,
                    "proto",
                    protocol,
                    "from",
                    subnet,
                    "to",
                    host,
                    "port",
                    "53",
                ],
                [
                    "ufw-user-input",
                    "-i",
                    interface,
                    "-s",
                    subnet,
                    "-d",
                    host,
                    "-p",
                    protocol,
                    "--dport",
                    "53",
                    "-j",
                    "ACCEPT",
                ],
            )
        )
    rules.append(
        (
            [
                "route",
                "allow",
                "in",
                "on",
                interface,
                "out",
                "on",
                uplink,
                "from",
                subnet,
            ],
            [
                "ufw-user-forward",
                "-i",
                interface,
                "-o",
                uplink,
                "-s",
                subnet,
                "-j",
                "ACCEPT",
            ],
        )
    )
    return rules


class FirewallBackend(ABC):
    """Abstract base class for host firewall management."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Return the current firewall status and active rules."""
        ...

    @abstractmethod
    def preflight(self, installed: bool = False) -> None:
        """Validate firewall prerequisites and state before convergence."""
        ...

    @abstractmethod
    def converge_service_rules(
        self,
        rules: list[dict[str, Any]],
        logging: str = "off",
        profiles: str = "skip",
        on_action: Callable[[], None] | None = None,
        service_ready_fn: Callable[[], None] | None = None,
    ) -> None:
        """Converge incoming/outgoing service rules."""
        ...

    @abstractmethod
    def converge_hotspot_rules(
        self,
        interface: str,
        uplink: str,
        address: str,
        on_action: Callable[[], None] | None = None,
    ) -> None:
        """Converge pinhole and packet routing rules for a Wi-Fi hotspot."""
        ...


class UfwBackend(FirewallBackend):
    """UFW (Uncomplicated Firewall) implementation of FirewallBackend."""

    def __init__(
        self,
        runner: Native | None = None,
        files: Files | None = None,
        systemd: Any | None = None,
        ready_unit_fn: Callable[[str], Any] | None = None,
    ):
        self.runner = runner or Native()
        self.files = files or Files()
        self.systemd = systemd
        self.ready_unit_fn = ready_unit_fn

    def _unit_state(self, name: str) -> dict[str, str]:
        if self.systemd and hasattr(self.systemd, "show"):
            return self.systemd.show(
                name, properties=["LoadState", "ActiveState", "UnitFileState"]
            )
        if hasattr(self.runner, "unit"):
            return getattr(self.runner, "unit")(name)
        return {}

    def _ready_unit(self, name: str) -> dict[str, str]:
        if self.ready_unit_fn:
            return self.ready_unit_fn(name)
        if self.systemd or hasattr(self.runner, "unit"):
            state = self._unit_state(name)
            if state.get("LoadState") != "loaded" or state.get("UnitFileState") in (
                "masked",
                "masked-runtime",
            ):
                raise Conflict(f"required system unit {name} is missing or masked")
            return state
        return {}

    def snapshot(self) -> dict[str, Any]:
        return parse_ufw_status(self.runner.run("ufw", "status", "verbose").stdout)

    def preflight(self, installed: bool = False) -> None:
        defaults = assignments(self.files.read("/etc/default/ufw"))
        if defaults:
            if defaults.get("IPV6") != "yes" or defaults.get("MANAGE_BUILTINS") != "no":
                raise Conflict(
                    "UFW requires IPv6 and MANAGE_BUILTINS=no to preserve other owners"
                )
            for key, value in [
                ("DEFAULT_INPUT_POLICY", "DROP"),
                ("DEFAULT_OUTPUT_POLICY", "ACCEPT"),
                ("DEFAULT_FORWARD_POLICY", "DROP"),
            ]:
                if defaults.get(key) != value:
                    raise Conflict(
                        "UFW default policy adoption requires operator review"
                    )
        elif installed:
            raise Conflict("UFW defaults are unavailable")

        for name in (
            "ufw.conf",
            "user.rules",
            "user6.rules",
            "before.rules",
            "before6.rules",
            "after.rules",
            "after6.rules",
        ):
            self.files.read("/etc/ufw/" + name)

        state = self._unit_state("nftables.service")
        if state.get("ActiveState") in ("active", "activating") or state.get(
            "UnitFileState"
        ) in ("enabled", "enabled-runtime"):
            raise Conflict("standalone nftables service conflicts with UFW ownership")

        if installed:
            self._ready_unit("ufw.service")
            self.snapshot()

    def kernel_rule(self, rule: dict[str, Any], v6: bool = False) -> bool:
        chain = "ufw6-user-input" if v6 else "ufw-user-input"
        port = format_port_range(rule["fromPort"], rule.get("toPort"))
        return check_kernel_rule(
            chain=chain,
            protocol=rule["protocol"],
            port=port,
            v6=v6,
            runner=self.runner,
        )

    def kernel_policy(self) -> bool:
        return check_kernel_policy(runner=self.runner)

    def converge_service_rules(
        self,
        rules: list[dict[str, Any]],
        logging: str = "off",
        profiles: str = "skip",
        on_action: Callable[[], None] | None = None,
        service_ready_fn: Callable[[], None] | None = None,
    ) -> None:
        f = self.files
        state = self.snapshot()
        pending = f.pending("firewall")

        for rule in rules:
            port = format_port_range(rule["fromPort"], rule.get("toPort"))
            expected = {(port, rule["protocol"], v6) for v6 in (False, True)}
            if not expected <= state["rules"]:
                f.mark("firewall")
                self.runner.run("ufw", "allow", "in", f"{port}/{rule['protocol']}")
                if on_action:
                    on_action()

        if state.get("logging") != logging:
            f.mark("firewall")
            self.runner.run("ufw", "logging", logging)
            if on_action:
                on_action()

        if state.get("profiles") != profiles:
            f.mark("firewall")
            self.runner.run("ufw", "app", "default", profiles)
            if on_action:
                on_action()

        if not state["active"]:
            f.mark("firewall")
            self.runner.run("ufw", "--force", "enable")
            if on_action:
                on_action()
        elif (
            pending
            or not self.kernel_policy()
            or any(
                not self.kernel_rule(rule, v6) for rule in rules for v6 in (False, True)
            )
        ):
            f.mark("firewall")
            self.runner.run("ufw", "reload")
            if on_action:
                on_action()

        if service_ready_fn:
            service_ready_fn()

        final = self.snapshot()
        expected_rules = {
            (
                format_port_range(rule["fromPort"], rule.get("toPort")),
                rule["protocol"],
                v6,
            )
            for rule in rules
            for v6 in (False, True)
        }
        if (
            not final["active"]
            or not self.kernel_policy()
            or final["policy"] != ("deny", "allow", "deny")
            or final["logging"] != logging
            or final["profiles"] != profiles
            or not expected_rules <= final["rules"]
            or any(
                not self.kernel_rule(rule, v6) for rule in rules for v6 in (False, True)
            )
        ):
            f.mark("firewall")
            raise Conflict("UFW policy or kernel rules did not converge")

    def converge_hotspot_rules(
        self,
        interface: str,
        uplink: str,
        address: str,
        on_action: Callable[[], None] | None = None,
    ) -> None:
        rules = hotspot_firewall_rules(interface, uplink, address)
        existing = set()
        for line in self.runner.run("ufw", "show", "added").stdout.splitlines():
            try:
                existing.add(canonical_rule(shlex.split(line)))
            except ValueError:
                continue

        for command, _ in rules:
            if canonical_rule(command) not in existing:
                self.files.mark("firewall")
                self.runner.run("ufw", *command)
                if on_action:
                    on_action()

        if any(
            self.runner.run("iptables", "-w", "5", "-C", *rule, check=False).returncode
            for _, rule in rules
        ):
            self.files.mark("firewall")
            self.runner.run("ufw", "reload")
            if on_action:
                on_action()

        if any(
            self.runner.run("iptables", "-w", "5", "-C", *rule, check=False).returncode
            for _, rule in rules
        ):
            raise Conflict("hotspot firewall rules did not converge")


class FirewalldBackend(FirewallBackend):
    """Firewalld backend adapter for Red Hat / Fedora / openSUSE families.

    TODO: Implement FirewalldBackend support using firewall-cmd / NetworkManager zones.
    """

    def __init__(self, runner: Native | None = None):
        self.runner = runner or Native()

    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError("FirewalldBackend is not yet implemented (TODO)")

    def preflight(self, installed: bool = False) -> None:
        raise NotImplementedError("FirewalldBackend is not yet implemented (TODO)")

    def converge_service_rules(
        self,
        rules: list[dict[str, Any]],
        logging: str = "off",
        profiles: str = "skip",
        on_action: Callable[[], None] | None = None,
        service_ready_fn: Callable[[], None] | None = None,
    ) -> None:
        raise NotImplementedError("FirewalldBackend is not yet implemented (TODO)")

    def converge_hotspot_rules(
        self,
        interface: str,
        uplink: str,
        address: str,
        on_action: Callable[[], None] | None = None,
    ) -> None:
        raise NotImplementedError("FirewalldBackend is not yet implemented (TODO)")


# Maintain backwards compatibility for callers/tests importing status
status = parse_ufw_status


def rule_port(rule: dict[str, Any]) -> str:
    return format_port_range(rule["fromPort"], rule.get("toPort"))


class Firewall:
    """High-level firewall adapter managing system service rules and hotspot rules."""

    def __init__(self, system: Any, backend: FirewallBackend | None = None):
        self.system = system
        self.files = getattr(system, "files", None)
        self.desired = (
            system.desired.get("firewall", {}) if hasattr(system, "desired") else {}
        )
        self.backend: FirewallBackend = backend or UfwBackend(
            runner=getattr(system, "native", None)
            or getattr(system, "runner", None)
            or (system if hasattr(system, "run") else None),
            files=self.files,
            systemd=getattr(system, "systemd", None),
            ready_unit_fn=getattr(system, "ready_unit", None),
        )

    def run(self, *args: Any, **kwargs: Any) -> Any:
        if hasattr(self.system, "run"):
            return self.system.run(*args, **kwargs)
        runner = getattr(self.backend, "runner", None)
        if runner and hasattr(runner, "run"):
            return runner.run(*args, **kwargs)
        raise RuntimeError("No runner available")

    def snapshot(self) -> dict[str, Any]:
        return self.backend.snapshot()

    def preflight(self, installed: bool = False) -> None:
        return self.backend.preflight(installed)

    def kernel_rule(self, rule: dict[str, Any], v6: bool = False) -> bool:
        if isinstance(self.backend, UfwBackend):
            return self.backend.kernel_rule(rule, v6)
        return False

    def kernel_policy(self) -> bool:
        if isinstance(self.backend, UfwBackend):
            return self.backend.kernel_policy()
        return True

    def converge(self) -> None:
        def on_action() -> None:
            if hasattr(self.system, "actions"):
                self.system.actions += 1

        def service_ready() -> None:
            if hasattr(self.system, "service"):
                self.system.service("ufw.service", "firewall")

        self.backend.converge_service_rules(
            rules=self.desired.get("rules", []),
            logging=self.desired.get("logging", "low"),
            profiles="skip",
            on_action=on_action,
            service_ready_fn=service_ready,
        )
        from .hotspot import converge_firewall

        converge_firewall(self.system)
        if self.files and hasattr(self.files, "clear"):
            self.files.clear("firewall")
