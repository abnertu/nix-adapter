# pyright: standard
"""Exercise the release gate with local tags and a synthetic GitHub CLI."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "scripts/wait-for-validation.py"

FAKE_GH = r"""
import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ["FIXTURE_ROOT"])
routes = json.loads((root / "routes.json").read_text())
calls = root / "calls.json"
counts = json.loads(calls.read_text()) if calls.exists() else {}
if len(sys.argv) != 3 or sys.argv[1] != "api":
    sys.exit("unexpected command")
endpoint = sys.argv[2]
if endpoint not in routes:
    sys.exit("unexpected API request")
index = counts.get(endpoint, 0)
counts[endpoint] = index + 1
calls.write_text(json.dumps(counts))
responses = routes[endpoint]
response = responses[min(index, len(responses) - 1)]
time.sleep(response.get("delay", 0))
if response.get("exit", 0):
    print("synthetic-private-output", file=sys.stderr)
    sys.exit(response["exit"])
print(response.get("raw", json.dumps(response.get("body"))))
"""


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        gh.chmod(0o755)
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "FIXTURE_ROOT": str(self.root),
        }
        self.git("init", "--quiet")
        workflow = self.repo / ".github/workflows/check.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: Historical Validation\n")
        self.git("add", ".github")
        self.git("commit", "--quiet", "-m", "tagged fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v0.8.0", "-m", "fixture")
        self.git("commit", "--quiet", "--allow-empty", "-m", "newer fixture")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.runs_path = (
            "repos/fixture/project/actions/runs?"
            f"head_sha={self.sha}&event=push&branch=main&per_page=100&page=1"
        )
        self.run_path = "repos/fixture/project/actions/runs/22"
        self.jobs_path = (
            "repos/fixture/project/actions/runs/22/attempts/1/jobs?per_page=100&page=1"
        )
        self.validation_run = {
            "id": 22,
            "run_number": 2,
            "run_attempt": 1,
            "workflow_id": 11,
            "path": ".github/workflows/check.yml",
            "head_sha": self.sha,
            "head_branch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }
        self.job = {
            "name": "Validate the library on x86_64-linux",
            "status": "completed",
            "conclusion": "success",
            "steps": [
                {
                    "name": "Run the flake checks",
                    "status": "completed",
                    "conclusion": "success",
                }
            ],
        }
        self.routes = {}
        self.install_routes()

    def install_routes(self):
        self.route(
            "repos/fixture/project/actions/workflows/11",
            {"id": 11, "path": self.validation_run["path"]},
        )
        self.route(
            self.runs_path, {"total_count": 1, "workflow_runs": [self.validation_run]}
        )
        self.route(self.run_path, self.validation_run)
        self.route(self.jobs_path, {"total_count": 1, "jobs": [self.job]})

    def select_current_contract(self):
        (self.repo / ".github/workflows/validate-library.yml").write_text(
            "name: Validate Library\n"
        )
        self.git("add", ".github")
        self.git("commit", "--quiet", "-m", "rename validation workflow")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "--force", "v0.8.0", self.sha)
        self.runs_path = (
            "repos/fixture/project/actions/runs?"
            f"head_sha={self.sha}&event=push&branch=main&per_page=100&page=1"
        )
        self.validation_run |= {
            "head_sha": self.sha,
            "path": ".github/workflows/validate-library.yml",
        }
        self.job["name"] = "Validate the Library on x86_64-linux"
        self.job["steps"][0]["name"] = "Run the Flake Checks"
        self.routes.clear()
        self.install_routes()

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def route(self, path, *bodies):
        self.routes[path] = [{"body": copy.deepcopy(body)} for body in bodies]

    def invoke(self, *, tag="v0.8.0", timeout="2", **environment):
        (self.root / "routes.json").write_text(json.dumps(self.routes))
        return subprocess.run(
            [
                sys.executable,
                str(GATE),
                "--repository",
                "fixture/project",
                "--tag",
                tag,
                "--timeout",
                timeout,
                "--interval",
                "0.01",
            ],
            cwd=self.repo,
            env=self.env | environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def assert_blocked(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("synthetic-private-output", result.stderr)

    def test_dispatch_validates_old_annotated_tag_not_branch_or_dispatch_sha(self):
        self.git("branch", "v0.8.0", self.head)
        result = self.invoke(
            GITHUB_SHA=self.head, GITHUB_EVENT_NAME="workflow_dispatch"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.sha)

    def test_push_supports_a_lightweight_tag(self):
        self.git("tag", "v0.8.1", self.sha)
        result = self.invoke(
            tag="v0.8.1", GITHUB_SHA=self.head, GITHUB_EVENT_NAME="push"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.sha)

    def test_historical_tag_works_when_filename_endpoint_is_retired(self):
        retired = "repos/fixture/project/actions/workflows/check.yml"
        self.routes[retired] = [{"exit": 1, "raw": "HTTP 404"}]
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads((self.root / "calls.json").read_text())
        self.assertNotIn(retired, counts)
        self.assertIn("repos/fixture/project/actions/workflows/11", counts)

    def test_current_tag_requires_current_contract_even_with_legacy_file_present(self):
        self.select_current_contract()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.sha)

    def test_current_tag_rejects_legacy_and_unrelated_green_workflows(self):
        self.select_current_contract()
        for path in (".github/workflows/check.yml", ".github/workflows/other.yml"):
            with self.subTest(path=path):
                self.route(
                    self.runs_path,
                    {
                        "total_count": 1,
                        "workflow_runs": [self.validation_run | {"path": path}],
                    },
                )
                self.assert_blocked(self.invoke(timeout="0.2"))

    def test_current_validation_rejects_legacy_job_or_skipped_check_step(self):
        self.select_current_contract()
        for job in (
            self.job | {"name": "Validate the library on x86_64-linux"},
            self.job | {"steps": [self.job["steps"][0] | {"conclusion": "skipped"}]},
        ):
            with self.subTest(job=job):
                self.route(self.jobs_path, {"total_count": 1, "jobs": [job]})
                self.assert_blocked(self.invoke())

    def test_unsupported_tagged_tree_never_queries_github(self):
        self.git("rm", ".github/workflows/check.yml")
        self.git("commit", "--quiet", "-m", "remove validation")
        self.git("tag", "v0.9.0")
        self.assert_blocked(self.invoke(tag="v0.9.0"))
        self.assertFalse((self.root / "calls.json").exists())

    def test_workflow_metadata_must_confirm_the_historical_identity(self):
        for metadata in (
            {"id": 12, "path": ".github/workflows/check.yml"},
            {"id": 11, "path": ".github/workflows/other.yml"},
        ):
            with self.subTest(metadata=metadata):
                self.route("repos/fixture/project/actions/workflows/11", metadata)
                self.assert_blocked(self.invoke())

    def test_missing_or_invalid_tag_never_queries_github(self):
        for tag in ("v0.9.0", "main", "--help", "v01.2.3"):
            with self.subTest(tag=tag):
                self.assert_blocked(self.invoke(tag=tag))
        self.assertFalse((self.root / "calls.json").exists())

    def test_waits_for_missing_and_pending_validation(self):
        pending = self.validation_run | {"status": "in_progress", "conclusion": None}
        self.route(
            self.runs_path,
            {"total_count": 0, "workflow_runs": []},
            {"total_count": 1, "workflow_runs": [pending]},
            {"total_count": 1, "workflow_runs": [self.validation_run]},
        )
        self.route(self.run_path, pending, self.validation_run)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads((self.root / "calls.json").read_text())
        self.assertGreaterEqual(counts[self.runs_path], 3)

    def test_newest_matching_failure_overrides_an_older_success(self):
        older = self.validation_run | {"id": 21, "run_number": 1}
        self.route("repos/fixture/project/actions/runs/21", older)
        self.route(
            self.jobs_path.replace("runs/22/", "runs/21/"),
            {"total_count": 1, "jobs": [self.job]},
        )
        self.route(
            self.runs_path,
            {"total_count": 2, "workflow_runs": [older, self.validation_run]},
        )
        for conclusion in ("failure", "cancelled", "skipped", "timed_out", "neutral"):
            with self.subTest(conclusion=conclusion):
                self.route(
                    self.run_path, self.validation_run | {"conclusion": conclusion}
                )
                self.assert_blocked(self.invoke())

    def test_ignores_green_runs_with_wrong_identity(self):
        for identity in (
            {"head_sha": self.head},
            {"workflow_id": 12},
            {"path": ".github/workflows/unrelated.yml"},
            {"head_branch": "feature"},
            {"event": "pull_request"},
        ):
            with self.subTest(identity=identity):
                self.route(
                    self.runs_path,
                    {
                        "total_count": 1,
                        "workflow_runs": [self.validation_run | identity],
                    },
                )
                self.assert_blocked(self.invoke(timeout="0.2"))

    def test_reads_all_pages_before_selecting_a_run(self):
        older = self.validation_run | {
            "id": 21,
            "run_number": 1,
            "conclusion": "failure",
        }
        self.route(self.runs_path, {"total_count": 2, "workflow_runs": [older]})
        self.route(
            self.runs_path[:-1] + "2",
            {"total_count": 2, "workflow_runs": [self.validation_run]},
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_current_rerun_attempt_and_rechecks_before_success(self):
        current = self.validation_run | {"run_attempt": 2}
        self.route(
            self.runs_path,
            {"total_count": 1, "workflow_runs": [self.validation_run]},
            {"total_count": 1, "workflow_runs": [current]},
        )
        self.route(self.run_path, self.validation_run, current, current)
        self.route(
            self.jobs_path.replace("attempts/1/", "attempts/2/"),
            {"total_count": 1, "jobs": [self.job]},
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads((self.root / "calls.json").read_text())
        self.assertIn(self.jobs_path.replace("attempts/1/", "attempts/2/"), counts)

    def test_new_run_appearing_during_verification_blocks_old_success(self):
        pending = self.validation_run | {
            "id": 23,
            "run_number": 3,
            "status": "in_progress",
            "conclusion": None,
        }
        self.route(
            self.runs_path,
            {"total_count": 1, "workflow_runs": [self.validation_run]},
            {"total_count": 2, "workflow_runs": [self.validation_run, pending]},
        )
        newest = "repos/fixture/project/actions/runs/23"
        self.route(
            newest, pending, pending | {"status": "completed", "conclusion": "failure"}
        )
        self.assert_blocked(self.invoke())
        counts = json.loads((self.root / "calls.json").read_text())
        self.assertGreaterEqual(counts[newest], 2)

    def test_success_requires_the_validation_job_and_its_step(self):
        cases = [
            [],
            [self.job | {"conclusion": "skipped"}],
            [self.job | {"steps": []}],
            [self.job | {"steps": [self.job["steps"][0] | {"conclusion": "skipped"}]}],
        ]
        for jobs in cases:
            with self.subTest(jobs=jobs):
                self.route(self.jobs_path, {"total_count": len(jobs), "jobs": jobs})
                self.assert_blocked(self.invoke())

    def test_rejects_incomplete_pages_and_malformed_metadata(self):
        for body in (
            {"total_count": 2, "workflow_runs": []},
            {"total_count": 1, "workflow_runs": [{}]},
            {"total_count": 1001, "workflow_runs": [self.validation_run]},
        ):
            with self.subTest(body=body):
                self.route(self.runs_path, body)
                self.assert_blocked(self.invoke())

    def test_api_errors_and_invalid_json_do_not_leak_responses(self):
        for response in ({"exit": 1}, {"raw": "synthetic-private-output"}):
            with self.subTest(response=response):
                self.routes[self.run_path] = [response]
                self.assert_blocked(self.invoke())

    def test_missing_validation_expires(self):
        self.route(self.runs_path, {"total_count": 0, "workflow_runs": []})
        self.assert_blocked(self.invoke(timeout="0.2"))

    def test_hung_gh_is_killed_within_the_gate_deadline(self):
        self.routes[self.run_path] = [{"delay": 10, "body": self.validation_run}]
        started = time.monotonic()
        self.assert_blocked(self.invoke(timeout="0.3"))
        self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
