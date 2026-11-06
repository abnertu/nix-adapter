# pyright: standard
"""Unit tests for nix_adapter.testing."""

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any, cast
from unittest import mock

from nix_adapter import Conflict
from nix_adapter.testing import FakeNative, FakeProcess


class TestingTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch(
            "subprocess.run", side_effect=AssertionError("native command")
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_fake_native_available(self):
        fake = FakeNative(available_commands=["curl", "systemctl"])
        self.assertTrue(fake.available("curl"))
        self.assertTrue(fake.available("systemctl"))
        self.assertFalse(fake.available("missing"))

    def test_fake_native_run_recording(self):
        fake = FakeNative(
            responses={
                ("echo", "hi"): "hi\n",
                ("fail",): FakeProcess(stdout="out", stderr="boom", returncode=1),
            }
        )
        res = fake.run("echo", "hi")
        self.assertEqual(res.stdout, "hi\n")
        self.assertEqual(fake.calls, [("echo", "hi")])

        failed_res = fake.run("fail", check=False)
        self.assertEqual(failed_res.returncode, 1)
        self.assertEqual(failed_res.stdout, "out")
        self.assertEqual(failed_res.stderr, "boom")

    def test_checked_failure_preserves_streams_and_redacts_values(self):
        for verbose in (False, True):
            with self.subTest(verbose=verbose):
                fake = FakeNative(
                    responses={
                        ("probe", "fixture-secret"): FakeProcess(
                            stdout="out fixture-secret",
                            stderr="err fixture-secret",
                            returncode=7,
                        )
                    }
                ).redacting("fixture-secret")
                fake.verbose = verbose
                stdout, stderr = StringIO(), StringIO()
                with (
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    self.assertRaises(Conflict) as raised,
                ):
                    fake.run("probe", "fixture-secret")
                message = str(raised.exception)
                self.assertIn("exit 7", message)
                self.assertIn("/usr/bin/probe", message)
                out, err = (
                    (stdout.getvalue(), stderr.getvalue())
                    if verbose
                    else (message, message)
                )
                self.assertIn("out <redacted>", out)
                self.assertIn("err <redacted>", err)
                self.assertNotIn("fixture-secret", message + out + err)

    def test_unsupported_calls_fail_before_recording(self):
        fake = FakeNative()
        for keyword in ("env", "timeout", "allow_failure", "unknown"):
            with self.subTest(keyword=keyword), self.assertRaises(TypeError):
                cast("Any", fake).run("probe", **{keyword: None})
        with self.assertRaises(ValueError):
            fake.run()
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
