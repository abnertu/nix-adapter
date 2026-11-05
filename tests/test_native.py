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


SECRET = "glrt-secret-token"


class RedactionTests(unittest.TestCase):
    """No configured secret leaves Native in a message or on a terminal."""

    def capture(self, native: Native, *args: str) -> tuple[str, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        message = ""
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                native.run(*args)
            except Conflict as error:
                message = str(error)
        return message, stdout.getvalue(), stderr.getvalue()

    def assert_redacted(self, *texts: str) -> None:
        for text in texts:
            self.assertNotIn(SECRET, text)
        self.assertIn("<redacted>", "".join(texts))

    @mock.patch("subprocess.run")
    def test_failure_message_redacts_argv_and_streams(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 1, f"out {SECRET}\n", f"err {SECRET}\n"
        )
        message, _, _ = self.capture(Native(redact=[SECRET]), "probe", SECRET)
        self.assert_redacted(message)

    @mock.patch("subprocess.run")
    def test_verbose_output_redacts_argv_and_streams(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, f"out {SECRET}\n", f"err {SECRET}\n"
        )
        native = Native(verbose=True, redact=[SECRET])
        _, stdout, stderr = self.capture(native, "probe", SECRET)
        self.assert_redacted(stdout, stderr)

    @mock.patch("subprocess.run")
    def test_timeout_redacts_partial_output_in_both_modes(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            [], 3, output=f"partial {SECRET}", stderr=f"partial {SECRET}"
        )
        for verbose in (False, True):
            native = Native(verbose=verbose, timeout=3, redact=[SECRET])
            self.assert_redacted(*self.capture(native, "probe"))

    @mock.patch("subprocess.run")
    def test_start_failure_redacts_argv(self, run):
        run.side_effect = FileNotFoundError("missing")
        message, _, _ = self.capture(Native(redact=[SECRET]), "missing", SECRET)
        self.assert_redacted(message)

    @mock.patch("subprocess.run")
    def test_longer_secrets_are_replaced_first(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, f"{SECRET}-suffix", "")
        native = Native(redact=[SECRET, f"{SECRET}-suffix"])
        message, _, _ = self.capture(native, "probe")
        self.assertNotIn("-suffix", message)

    @mock.patch("subprocess.run")
    def test_secrets_that_shell_quoting_rewrites_are_redacted(self, run):
        quoted = "ab'cd"
        run.return_value = subprocess.CompletedProcess([], 1, "", "")
        native = Native(verbose=True, redact=[quoted])
        message, _, stderr = self.capture(native, "probe", f"--token={quoted}")
        for text in (message, stderr):
            self.assertNotIn("ab'", text)
            self.assertNotIn("cd", text)
        run.side_effect = FileNotFoundError("missing")
        message, _, _ = self.capture(Native(redact=[quoted]), "missing", quoted)
        self.assertNotIn("cd", message)

    def test_empty_secrets_are_rejected_by_the_constructor(self):
        with self.assertRaises(ValueError):
            Native(redact=[""])

    @mock.patch("subprocess.run")
    def test_redacting_keeps_settings_and_subclasses(self, run):
        class Recording(Native):
            def __init__(self) -> None:
                super().__init__(verbose=True, timeout=7)

        run.return_value = subprocess.CompletedProcess([], 0, SECRET, "")
        native = Recording().redacting(SECRET, "")
        self.assertIsInstance(native, Recording)
        self.assertTrue(native.verbose)
        self.assertEqual(native.timeout, 7)
        _, stdout, _ = self.capture(native, "probe")
        self.assert_redacted(stdout)


if __name__ == "__main__":
    unittest.main()
