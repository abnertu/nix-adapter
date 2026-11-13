# nix-adapter

Python primitives for adapters that reconcile live host state with a manifest that Nix has evaluated.

[![Validate](https://github.com/abnertu/nix-adapter/actions/workflows/validate-library.yml/badge.svg?branch=main)](https://github.com/abnertu/nix-adapter/actions/workflows/validate-library.yml?query=branch%3Amain) [![Python](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fabnertu%2Fnix-adapter%2Fmain%2Fpyproject.toml&query=%24.project%5B%27requires-python%27%5D&label=Python&logo=python&logoColor=white&color=3776AB)](https://www.python.org/)

An adapter reads the manifest and checks the host. It then changes only what differs and stops with a full diagnostic on each conflict.

## Install

Requirements: Nix with flakes, or Python 3.13 or later with `rich`.

Add the flake input and use its Python environment:

```nix
{
  inputs.nix-adapter.url = "github:abnertu/nix-adapter";

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

`inspect_subordinate_range(path, user, desired)` reports whether one public sub-ID allocation matches without changing files.
`ensure_subordinate_range` uses the same validation before writing.
`subordinate_allocation(path, user)` returns the account's one allocation, or None, without changing files.
`remove_subordinate_range(path, user, desired)` removes the matching allocation and returns whether the file changed.
It refuses a drifted allocation. Removing the last allocation leaves an empty file.
Pass known numeric owner IDs through `aliases=("1001",)` to all four helpers.
All four helpers treat the named owner and those explicit aliases as one account.
A healthy numeric allocation keeps its original owner field.
All four helpers reject unsafe paths, malformed ranges, overlapping allocations, and multiple allocations for the managed account.
`ensure_subordinate_range` and `inspect_subordinate_range` also reject a desired range that overlaps another account.
Disjoint allocations for other accounts remain valid.
Ranges use canonical decimal positive IDs and counts, ending at or below 4,294,967,294.
Allocation records contain fewer than 1,024 UTF-8 bytes before LF.

## Testing adapters

`FakeNative` records commands and returns configured `FakeProcess` responses without executing native commands.
Its `run` method accepts positional command arguments and the keyword `check`.
Checked failures raise `Conflict` with redacted command output.
Use `check=False` to inspect a failed response.
Test real subprocess diagnostics with `Native` and a patched `subprocess.run`.

When upgrading from 0.7.x, update adapter tests for the fake's API.
Remove the previously ignored `env`, `timeout`, and other keyword arguments from fake calls.
Replace `allow_failure=True` with `check=False`.
Catch `Conflict` instead of `CalledProcessError` for checked fake failures.

## Releases

The release workflow waits for successful `validate-library.yml` validation of the exact tag commit on `main`.
Missing validation times out after 40 minutes.
Failed validation blocks publication.
The gate also requires successful execution of the flake-check step.
Push the release commit to `main` with its tag.
Rerun failed validation before retrying publication.
Use the release workflow's `workflow_dispatch` input to retry an existing tag without moving it.
Dispatch uses the selected workflow revision's tooling and validates the requested tag's commit.
Tags before the workflow rename retain the historical `check.yml` job and step contract.

## Ownership

nix-adapter does the mechanics: safe file replacement, command execution, diagnostics, plan output and the CLI phases.

The adapter that uses it owns all policy:

- each host path, marker name, drop-in name and lock path;
- the desired content of each file;
- each secret value that `Native` must redact.
