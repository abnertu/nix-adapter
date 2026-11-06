"""Wait for successful validation of an existing release tag's exact commit."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlencode


@dataclass(frozen=True)
class Contract:
    """The validation identity recorded in the tagged source tree."""

    path: str
    job: str
    step: str


CURRENT = Contract(
    ".github/workflows/validate-library.yml",
    "Validate the Library on x86_64-linux",
    "Run the Flake Checks",
)
LEGACY = Contract(
    ".github/workflows/check.yml",
    "Validate the library on x86_64-linux",
    "Run the flake checks",
)


class GateError(Exception):
    """Validation could not establish permission to publish."""


def record(value: object) -> dict[str, object]:
    """Require a JSON object without exposing malformed response contents."""
    if not isinstance(value, dict):
        raise GateError("validation metadata is not an object")
    return cast("dict[str, object]", value)


def number(value: object) -> int:
    """Require a positive metadata identifier or counter."""
    if type(value) is not int or value < 1:
        raise GateError("validation metadata has an invalid identifier or counter")
    return value


def text(value: object) -> str:
    """Require a nonempty metadata string."""
    if not isinstance(value, str) or not value:
        raise GateError("validation metadata has an invalid string")
    return value


@dataclass(frozen=True)
class Deadline:
    """One monotonic deadline shared by polling and every subprocess."""

    end: float

    def remaining(self) -> float:
        """Return the remaining seconds or reject exhausted coverage."""
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise GateError("timed out waiting for tag validation")
        return remaining

    def command(self, *argv: str) -> str:
        """Capture a bounded command, suppressing untrusted failure output."""
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=min(30, self.remaining()),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GateError(
                f"{argv[0]} could not complete within its deadline"
            ) from error
        if result.returncode:
            raise GateError(f"{argv[0]} failed while checking tag validation")
        return result.stdout


def tag_commit(tag: str, deadline: Deadline) -> str:
    """Peel an explicitly named version tag, never a branch or dispatch SHA."""
    component = r"(?:0|[1-9][0-9]*)"
    if not re.fullmatch(rf"v{component}\.{component}\.{component}", tag):
        raise GateError("--tag must have the form vX.Y.Z")
    sha = deadline.command(
        "git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"
    )
    sha = sha.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GateError(f"refs/tags/{tag} did not resolve to a commit SHA")
    return sha


def tag_contract(sha: str, deadline: Deadline) -> Contract:
    """Select only an explicit current or historical validation contract."""
    paths = set(
        deadline.command(
            "git", "ls-tree", "--name-only", sha, "--", CURRENT.path, LEGACY.path
        ).splitlines()
    )
    if CURRENT.path in paths:
        return CURRENT
    if LEGACY.path in paths:
        return LEGACY
    raise GateError(f"{sha}: no supported validation workflow in the tagged tree")


@dataclass(frozen=True)
class Run:
    """Validated identity and outcome of one workflow run attempt."""

    id: int
    number: int
    attempt: int
    workflow: int
    path: str
    sha: str
    branch: str
    event: str
    status: str
    conclusion: object

    @classmethod
    def parse(cls, value: object) -> Run:
        """Reject missing identity fields rather than accepting partial coverage."""
        item = record(value)
        return cls(
            number(item.get("id")),
            number(item.get("run_number")),
            number(item.get("run_attempt")),
            number(item.get("workflow_id")),
            text(item.get("path")),
            text(item.get("head_sha")),
            text(item.get("head_branch")),
            text(item.get("event")),
            text(item.get("status")),
            item.get("conclusion"),
        )

    def matches(self, sha: str, path: str) -> bool:
        """Whether this is the required workflow's push/main run for the tag."""
        return (
            self.sha == sha
            and self.path == path
            and self.branch == "main"
            and self.event == "push"
        )


@dataclass(frozen=True)
class GitHub:
    """Read workflow metadata through gh without handling credential values."""

    repository: str
    deadline: Deadline

    def api(self, path: str) -> dict[str, object]:
        """Read one JSON object, keeping API bodies out of error diagnostics."""
        response = self.deadline.command("gh", "api", f"repos/{self.repository}/{path}")
        try:
            value: object = json.loads(response)
        except ValueError as error:
            raise GateError(f"{path}: GitHub returned invalid JSON metadata") from error
        return record(value)

    def pages(self, path: str, key: str) -> list[object]:
        """Read every search page or reject incomplete or excessive coverage."""
        items: list[object] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 11):
            result = self.api(f"{path}{separator}per_page=100&page={page}")
            total = result.get("total_count")
            batch = result.get(key)
            if (
                type(total) is not int
                or not 0 <= total <= 1000
                or not isinstance(batch, list)
            ):
                raise GateError(
                    f"{path}: GitHub returned incomplete validation coverage"
                )
            entries = cast("list[object]", batch)
            items.extend(entries)
            if len(items) == total:
                return items
            if not entries or len(items) > total:
                raise GateError(f"{path}: GitHub returned inconsistent pagination")
        raise GateError(f"{path}: metadata exceeds the supported search limit")

    def latest(self, sha: str, contract: Contract) -> Run | None:
        """Select exact-path coverage even after a filename endpoint retires."""
        query = urlencode({"head_sha": sha, "event": "push", "branch": "main"})
        runs = [
            Run.parse(item)
            for item in self.pages(f"actions/runs?{query}", "workflow_runs")
        ]
        matching = [run for run in runs if run.matches(sha, contract.path)]
        identities = {run.workflow for run in matching}
        if len(identities) > 1:
            raise GateError(
                f"{contract.path}: validation workflow identity is ambiguous"
            )
        if identities:
            identity = identities.pop()
            workflow = self.api(f"actions/workflows/{identity}")
            if (
                number(workflow.get("id")) != identity
                or workflow.get("path") != contract.path
            ):
                raise GateError(
                    f"{contract.path}: validation workflow identity changed"
                )
        return max(matching, key=lambda run: run.number, default=None)

    def current(self, selected: Run) -> Run:
        """Refresh an attempt and enforce the selected run's identity."""
        run = Run.parse(self.api(f"actions/runs/{selected.id}"))
        if (
            run.id != selected.id
            or run.workflow != selected.workflow
            or not run.matches(selected.sha, selected.path)
        ):
            raise GateError(f"validation run {selected.id} identity changed")
        return run

    def checked_job(self, run: Run, contract: Contract) -> None:
        """Require the actual flake-check step in the current successful attempt."""
        jobs = self.pages(f"actions/runs/{run.id}/attempts/{run.attempt}/jobs", "jobs")
        required = [
            record(job) for job in jobs if record(job).get("name") == contract.job
        ]
        if len(required) != 1:
            raise GateError(f"required job {contract.job} is missing or ambiguous")
        job = required[0]
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise GateError(f"required job {contract.job} did not succeed")
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise GateError(f"required job {contract.job} has no steps")
        required_steps = [
            record(step)
            for step in cast("list[object]", steps)
            if record(step).get("name") == contract.step
        ]
        if len(required_steps) != 1:
            raise GateError(f"required step {contract.step} is missing or ambiguous")
        step = required_steps[0]
        if step.get("status") != "completed" or step.get("conclusion") != "success":
            raise GateError(f"required step {contract.step} did not succeed")


def wait(github: GitHub, sha: str, contract: Contract, interval: float) -> None:
    """Wait for current, complete coverage; terminal failures reject publication."""
    while True:
        selected = github.latest(sha, contract)
        if selected is not None:
            run = github.current(selected)
            if run.status == "completed":
                if run.conclusion != "success":
                    raise GateError(f"validation run {run.id} did not succeed")
                github.checked_job(run, contract)
                if github.current(run) == run and github.latest(sha, contract) == run:
                    return
            elif run.status not in {
                "queued",
                "in_progress",
                "waiting",
                "pending",
                "requested",
            }:
                raise GateError(f"validation run {run.id} has an unsupported state")
        time.sleep(min(interval, github.deadline.remaining()))


def positive(value: str) -> float:
    """Parse a finite positive duration for command-line deadline controls."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("duration must be finite and positive")
    return result


def main() -> int:
    """Print the validated tag SHA, or one redacted failure with a nonzero exit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--timeout", type=positive, default=2400.0)
    parser.add_argument("--interval", type=positive, default=10.0)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            raise GateError("--repository is not a repository name")
        deadline = Deadline(time.monotonic() + args.timeout)
        sha = tag_commit(args.tag, deadline)
        wait(
            GitHub(args.repository, deadline),
            sha,
            tag_contract(sha, deadline),
            args.interval,
        )
        if tag_commit(args.tag, deadline) != sha:
            raise GateError(f"refs/tags/{args.tag} changed during validation")
        print(sha)
    except GateError as error:
        print(f"release validation: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
