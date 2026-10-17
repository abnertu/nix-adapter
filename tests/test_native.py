"""Contract tests for Native command runner."""

import contextlib
import io
import subprocess
import unittest
from unittest import mock

from nix_adapter import Conflict, Native


class NativeTests(unittest.TestCase):
    def result(
        self, code: int = 0, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["command"], code, stdout, stderr)

    @mock.patch("subprocess.run")
    def test_failure_contains_both_complete_streams(self, run):
        run.return_value = self.result(7, "stdout line\n", "stderr line\n")
        with self.assertRaises(Conflict) as raised:
            Native().run("false")
        message = str(raised.exception)
        self.assertIn("exit 7", message)
        self.assertIn("/usr/bin/false", message)
        self.assertIn("stdout:\nstdout line", message)
        self.assertIn("stderr:\nstderr line", message)

    @mock.patch("subprocess.run")
    def test_check_false_preserves_completed_process(self, run):
        run.return_value = self.result(9, "out", "err")
        result = Native().run("probe", check=False)
        self.assertEqual(result.returncode, 9)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.stderr, "err")

    @mock.patch("subprocess.run")
    def test_verbose_prints_command_and_complete_streams(self, run):
        run.return_value = self.result(0, "out\n", "err\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            Native(verbose=True).run("probe", "argument")
        self.assertEqual(stdout.getvalue(), "out\n")
        self.assertIn("+ /usr/bin/probe argument", stderr.getvalue())
        self.assertIn("err\n", stderr.getvalue())

    @mock.patch("subprocess.run")
    def test_timeout_contains_partial_output(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            ["/usr/bin/probe"], 3, output="partial out", stderr="partial err"
        )
        with self.assertRaises(Conflict) as raised:
            Native(timeout=3).run("probe")
        message = str(raised.exception)
        self.assertIn("timed out after 3s", message)
        self.assertIn("partial out", message)
        self.assertIn("partial err", message)


if __name__ == "__main__":
    unittest.main()
