# nix-adapter

Python primitives for adapters that reconcile live host state against declarations Nix has already evaluated. Nix generates a manifest; an adapter built on this library checks the host, applies only what differs and reports failures with full diagnostics.

## Modules

| Module | Provides |
| --- | --- |
| `base` | `BaseAdapter[M]`: a manifest of type M, file and command access, systemd unit convergence, `plan_file`/`plan_unit` |
| `service` | `BaseServiceAdapter`: one service with its account and unit files |
| `files` | `Files`: owned paths, atomic writes, pending markers, drop-ins, config parsers |
| `io` | Low-level atomic and symlink-safe file operations |
| `native` | `Native`: command runner that keeps stdout, stderr and timeout output, with redaction |
| `systemd` | `Systemd`: unit inspection and control |
| `system` | Hostname, timezone, NTP, discard support, sub-UID/GID ranges |
| `cli` | `run_adapter_cli`: the `preflight` / `plan` / `converge` / `purge` entry point, plus adapter-specific phases |
| `plan` | `Change` and `render`: what converge would change, with `= ~ + ! -` markers |
| `progress` | `Progress`: a task display that yields the terminal to native command output |
| `lock` | `operation_lock`: an exclusive lock around one operation |
| `testing` | `FakeNative`, `FakeProcess` for adapter tests |
| `manifest` | `validate`: check a manifest's keys against the adapter's `TypedDict` |
| `toml` | `parse_toml`, `dump_toml` |

Errors that need an operator decision raise `Conflict`; an optional feature that was never prepared raises `NotReady`.

## Use from a flake

```nix
{
  inputs.nix-adapter.url = "github:abnertu/nix-adapter";

  outputs = { nix-adapter, ... }: {
    # Python with nix-adapter on its path:
    # nix-adapter.packages.x86_64-linux.adapterPython
  };
}
```

## Development

```bash
nix develop
python -m unittest discover -s tests
nix flake check
```

The only runtime dependency is `rich`.
