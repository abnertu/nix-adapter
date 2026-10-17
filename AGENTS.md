# AGENTS Instructions

Standalone Python + Nix library of reconciliation primitives.

- Keep it generic: feature policy (what a hotspot or firewall should look like) belongs to the consuming adapter, not here.
- Runtime dependencies: the standard library and `rich`, nothing else.
- Errors name the path, unit or command involved and never include secrets.
- Removing or renaming an export is a breaking change: bump the minor version and update consumers (`~/.config/nacre`, `~/codebase/gitlab-runner`).
- Validate with `ruff check`, `ruff format --check`, `python -m unittest discover -s tests` and `nix flake check`.
- Commits follow Conventional Commits with no attribution trailers. Push only when the user asks.
