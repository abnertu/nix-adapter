# AGENTS Instructions

nix-adapter is a generic Python and Nix library of reconciliation primitives.

## Library contracts

- Keep the library generic. Feature policy belongs to the adapter that uses it, for example the desired state of a hotspot or a firewall.
- Take each host path, marker name, drop-in name and lock path as a required argument. Give it no default.
- A missing argument then fails loudly. A default would silently write state at a new location.
- Keep the runtime dependencies to the standard library and `rich`.
- Name the path, unit, command or key in each error.
- Never put a secret in an error. Keep the values of environment-style files out of messages.
- Send command output through `Native` redaction.
- Removing or renaming an export is a breaking change. Bump the minor version, and update each project that uses the library.

## Git

- Write commits in [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) format.
- For a non-trivial change, write a commit body that explains why.
- Mark a breaking change with `!` and a `BREAKING CHANGE:` footer.
- Author commits as the user alone, without `Co-Authored-By` or other co-author trailers.
- Commit as the plan progresses.
- Before you push, squash related commits into one commit to keep the history clean.
- Push only after the user approves that push.
- Version releases with [Semantic Versioning 2.0.0](https://semver.org/).
- `pyproject.toml` holds the only version literal.
- Use `just release X.Y.Z` to set the version, prepend git-cliff notes, commit and tag locally.
- Push the branch and its tag together with `git push --atomic`.
- Never move a pushed tag. If the release workflow fails, fix it on `main` and run it again with `workflow_dispatch`.

## Tests

- Add a test to the repo only when it guards behavior that no existing test covers.
- The test must fail when that behavior breaks. Weigh this before you add each test.
- Run throwaway tests freely to check your work.
- Keep throwaway tests out of the repo unless they meet the bar above.
- Validate with `nix flake check`. It runs the unit tests, ruff, pyright strict, statix, deadnix and nixfmt.
- `python -m unittest` in the dev shell tests the installed store copy, not the working tree.
- Flakes see tracked files only. Stage each new file with `git add` before a Nix command.
- `FakeNative.run` accepts the same arguments as `Native.run` and raises `Conflict` on checked failures.
- Test subprocess diagnostics with `Native` and a patched `subprocess.run`.

## CI

- Keep a CI check only when it catches a concrete failure that no existing check covers.
- Identify that failure before you add or keep the check.
- Apply the test rules to CI checks too.
- Run the repo's entry points against real inputs or isolated fixtures.
- Reject duplicate checks, source-text assertions, copied inventories and checks that only prove that a command or file exists.
- Name CI files in kebab-case by purpose, such as `deploy-production.yml`.
- Keep reserved host filenames, including `.gitlab-ci.yml`.
- Name workflows in Title Case, such as `Deploy Production`.
- Keep the repository name out of workflow names.
- Name job IDs in snake_case, such as `build_image`.
- Start each GitHub step name with a verb, such as `Install Dependencies` or `Run Tests`.
- Preserve proper nouns and code identifiers when applying case.
- Prefer a specific purpose, such as `deploy-production.yml`, over a vague name, such as `production.yml`.
- Omit redundant host or artifact words, as in `github-action-build.yml` and `build-workflow.yml`.
- Name each workflow, job and step by its target and action.
- Keep job display names unique within the repository.
- Update dispatch references, dependencies, release gates, required check contexts and badges together when renaming CI artifacts.
- Propagate command failures.
- Count a missing prerequisite or skipped required coverage as a failure.
- After you change these rules, audit the repo against them.
- Validate changes locally. Then verify the required CI on the pushed commit.
- Report each uncovered contract and each unavailable check.

## Docs

- Keep each doc concise and plain. Each sentence must carry information.
- Write English docs in US English, to the [ASD-STE100](https://www.asd-ste100.org/) writing rules:
  - Give one instruction in each sentence.
  - Write instructions in the imperative and in the active voice.
  - Use one word for one meaning, and one meaning for one word.
  - An instruction has 20 words or fewer. A description has 25 words or fewer.
- Review each sentence over these limits. Split it if it holds more than one instruction or fact. A list of items can stay in one sentence. Make a bullet list from more than five items.
- Write a non-English doc in two passes:
  1. Write an English draft to these rules.
  2. Translate the draft. Keep its sentence boundaries and one term for each concept. Keep commands, paths and code unchanged. Use the natural word order of the target language.
