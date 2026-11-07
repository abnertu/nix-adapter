# Changelog

## 0.8.0 (2026-11-07)

### Features

- **system**: Expose read-only subordinate range inspection

### Fixes

- **testing**: Align fake native calls with the runtime contract (breaking)
- **system**: Validate native subordinate ID records

## 0.7.0 (2026-11-05)

### Features

- Redact configured secrets from native diagnostics

### Refactoring

- Move consumer paths and markers out of the library (breaking)
- Type the adapter protocol and pass the invocation context (breaking)
- Name the source, line and key in every conflict (breaking)
- Document every public symbol and use one name for the runner (breaking)

### Documentation

- Rewrite the README and agent rules to the repo-docs template

Earlier history is in `git log`.
