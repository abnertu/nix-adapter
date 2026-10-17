"""Unit tests for nix_adapter.cli."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from nix_adapter.cli import run_adapter_cli
from nix_adapter.exceptions import Conflict, NotReady


class DummyAdapter:
    def __init__(self, desired):
        self.desired = desired

    def preflight(self):
        if self.desired.get("not_ready"):
            raise NotReady("prerequisites missing")
        if self.desired.get("conflict"):
            raise Conflict("conflict occurred")
        return self.desired.get("ready", True)

    def converge(self):
        if self.desired.get("conflict"):
            raise Conflict("converge conflict")


class CliTests(unittest.TestCase):
    def test_run_adapter_cli_success(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "manifest.json"
            manifest.write_text(json.dumps({"ready": True}))

            sys.argv = ["adapter.py", str(manifest), "preflight"]
            code = run_adapter_cli(DummyAdapter, require_root=False)
            self.assertEqual(code, 0)

            sys.argv = ["adapter.py", str(manifest), "converge"]
            code = run_adapter_cli(DummyAdapter, require_root=False)
            self.assertEqual(code, 0)

    def test_purge_runs_only_on_adapters_that_implement_it(self):
        class Purging(DummyAdapter):
            purged = False

            def purge(self):
                Purging.purged = True

        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "manifest.json"
            manifest.write_text(json.dumps({}))
            sys.argv = ["adapter.py", str(manifest), "purge"]
            self.assertEqual(run_adapter_cli(Purging, require_root=False), 0)
            self.assertTrue(Purging.purged)
            with self.assertRaisesRegex(Conflict, "does not support purge"):
                run_adapter_cli(DummyAdapter, require_root=False)

    def test_run_adapter_cli_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "manifest.json"
            manifest.write_text(json.dumps({"not_ready": True}))

            sys.argv = ["adapter.py", str(manifest), "preflight"]
            code = run_adapter_cli(DummyAdapter, require_root=False)
            self.assertEqual(code, 20)

    def test_run_adapter_cli_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "manifest.json"
            manifest.write_text(json.dumps({"conflict": True}))

            sys.argv = ["adapter.py", str(manifest), "converge"]
            code = run_adapter_cli(DummyAdapter, require_root=False)
            self.assertEqual(code, 1)

    def test_run_adapter_cli_allow_skip(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "manifest.json"
            manifest.write_text(json.dumps({"ready": False}))

            sys.argv = ["adapter.py", str(manifest), "preflight"]
            code = run_adapter_cli(DummyAdapter, allow_skip=True, require_root=False)
            self.assertEqual(code, 20)


if __name__ == "__main__":
    unittest.main()
