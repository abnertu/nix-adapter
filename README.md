# nix-adapter

Python primitives for adapters that reconcile live host state with a manifest that Nix has evaluated.

[![nix-adapter validation](https://github.com/abnertu/nix-adapter/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/abnertu/nix-adapter/actions/workflows/check.yml) [![Python](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fabnertu%2Fnix-adapter%2Fmain%2Fpyproject.toml&query=%24.project%5B%27requires-python%27%5D&label=Python&logo=python&logoColor=white&color=3776AB)](https://www.python.org/)

An adapter reads the manifest and checks the host. It then changes only what differs and stops with a full diagnostic on each conflict.

## Install

Requirements: Nix with flakes, or Python 3.13 or later with `rich`.

Add the flake input and use its Python environment:

```nix
{
  inputs.nix-adapter.url = "github:abnertu/nix-adapter?ref=v0.7.0";

  outputs = { nix-adapter, ... }: {
    # Python 3 with nix-adapter on its path:
    # nix-adapter.packages.x86_64-linux.adapterPython
  };
}
```

## Usage

Subclass `BaseAdapter`, and start it with `run_adapter_cli`:

```python
from collections.abc import Mapping

from nix_adapter import AdapterContext, BaseAdapter, Change, run_adapter_cli


class Motd(BaseAdapter[Mapping[str, object]]):
    pending_template = "/var/lib/example/{action}.pending"

    def plan(self) -> list[Change]:
        return [self.plan_file("/etc/motd", str(self.desired["text"]))]

    def converge(self) -> None:
        self.write("/etc/motd", str(self.desired["text"]), "motd")


def load(manifest: Mapping[str, object], context: AdapterContext) -> Motd:
    return Motd(manifest, context=context)


raise SystemExit(run_adapter_cli(load, "Motd"))
```

Run it as root with a manifest file and a phase:

```bash
python motd.py manifest.json plan --color
python motd.py manifest.json converge --verbose
```

The phases are `preflight`, `plan` and `converge`. An adapter can add `purge` (the `Purgeable` protocol) and named phases (the `ExtraPhases` protocol).

| Exit status | Meaning |
| --- | --- |
| 0 | The phase completed. |
| 1 | A conflict or a failure. The diagnostic is on stderr, or on stdout as a `!` plan line. |
| 20 | An optional adapter is not ready, because its preparation never completed. |

## Modules

| Module | Provides |
| --- | --- |
| `base` | `BaseAdapter[M]`: manifest access, files, commands, unit convergence, `plan_file` and `plan_unit` |
| `service` | `BaseServiceAdapter`: one service with its account and unit files |
| `cli` | `run_adapter_cli` and the `Adapter`, `Purgeable` and `ExtraPhases` protocols |
| `context` | `AdapterContext`: the `--verbose` and `--color` settings of one invocation |
| `files` | `Files`: owned paths, atomic writes, pending markers, drop-in checks and config parsers |
| `io` | Atomic, symlink-safe file operations |
| `native` | `Native`: a command runner that keeps stdout, stderr and timeout output, with redaction |
| `systemd` | `Systemd`: unit inspection and control |
| `system` | Hostname, timezone, NTP, discard support and subordinate ID ranges |
| `plan` | `Change` and `render`: plan lines with `= ~ + ! -` markers |
| `progress` | `Progress`: a task display that yields the terminal to command output |
| `lock` | `operation_lock`: an exclusive lock around one operation |
| `manifest` | `validate`: check manifest keys against a `TypedDict` |
| `testing` | `FakeNative` and `FakeProcess` for adapter tests |
| `toml` | `parse_toml` and `dump_toml` |

An error that needs an operator decision raises `Conflict`. An optional feature without completed preparation raises `NotReady`.

## Ownership

nix-adapter does the mechanics: safe file replacement, command execution, diagnostics, plan output and the CLI phases.

The adapter that uses it owns all policy:

- each host path, marker name, drop-in name and lock path;
- the desired content of each file;
- each secret value that `Native` must redact.
