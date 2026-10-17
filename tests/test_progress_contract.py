"""Check the real Bash bridge with foreground I/O, traps, and process signals."""

import os
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SH_PATH = Path(__file__).parent / "progress.sh"


class ProgressContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="progress contract ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.renderer = self.root / "renderer"
        self.renderer.write_text(
            f"#!{shutil.which('sh')}\nexec "
            + shlex.join([sys.executable, "-m", "nix_adapter.progress"])
            + ' "$@"\n'
        )
        self.renderer.chmod(0o755)
        self.preamble = (
            "set -euo pipefail\n"
            f"progress_renderer={shlex.quote(str(self.renderer))}\n"
            f"source {shlex.quote(str(SH_PATH))}\n"
            "progress_init fixture 0\n"
            "trap 'exit 130' INT\ntrap 'exit 143' TERM\n"
            "trap 'status=$?; progress_exit \"$status\"' EXIT\n"
        )

    def run_script(self, script, **kwargs):
        return subprocess.run(
            ["bash", "-c", self.preamble + script],
            text=True,
            capture_output=True,
            timeout=5,
            **kwargs,
        )

    def test_foreground_input_and_both_output_streams_survive_handoff(self):
        result = self.run_script(
            """
progress_start 'Check synthetic inputs'
progress_suspend
printf 'answer: ' >&2
IFS= read -r answer
printf 'stdout: %s\\n' "$answer"
printf 'stderr: intact\\n' >&2
progress_resume
progress_finish done
""",
            input="fixture answer\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "stdout: fixture answer\n")
        self.assertIn("answer: stderr: intact\n", result.stderr)
        self.assertIn("Check synthetic inputs", result.stderr)
        self.assertNotIn("\x1b", result.stderr)

    def test_task_failure_preserves_exit_code_and_diagnostics(self):
        result = self.run_script(
            """
progress_start 'Fail synthetic task'
progress_suspend
printf 'partial stdout\\n'
printf 'failure stderr\\n' >&2
exit 37
"""
        )
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, "partial stdout\n")
        self.assertIn("failure stderr", result.stderr)
        self.assertIn("failed", result.stderr.lower())
        self.assertNotIn("[done]", result.stderr)

    def test_terminal_animation_yields_until_foreground_prompt_finishes(self):
        master, slave = pty.openpty()
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"CI", "NO_COLOR"}
        }
        environment["TERM"] = "xterm-256color"
        process = subprocess.Popen(
            [
                "bash",
                "-c",
                self.preamble
                + """
progress_start 'Read synthetic answer'
progress_suspend
printf 'fixture prompt: ' >&2
IFS= read -r answer
printf 'answer=%s\\n' "$answer"
progress_resume
progress_finish 'done'
""",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=slave,
            env=environment,
        )
        os.close(slave)
        try:
            output = b""
            while not output.endswith(b"fixture prompt: "):
                self.assertTrue(select.select([master], [], [], 5)[0], output)
                output += os.read(master, 4096)
            self.assertFalse(select.select([master], [], [], 0.25)[0])
            stdout, _ = process.communicate(b"sample\n", timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout, b"answer=sample\n")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)

    def test_exit_cleanup_retains_original_status_and_closes_renderer(self):
        result = self.run_script(
            """
cleanup() {
  local status=$?
  progress_exit "$status"
  printf 'original cleanup\\n' >&2
  return "$status"
}
trap cleanup EXIT
progress_start 'Interrupted work'
exit 75
"""
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("original cleanup", result.stderr)

    def test_sigterm_does_not_leave_a_renderer_holding_output_open(self):
        process = subprocess.Popen(
            [
                "bash",
                "-c",
                self.preamble
                + "progress_start 'Wait for fixture signal'\n"
                + shlex.join(["bash", "-c", "printf 'ready\\n'; exec sleep 20"])
                + "\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        assert process.stdout is not None
        try:
            self.assertEqual(process.stdout.readline(), "ready\n")
            os.killpg(process.pid, signal.SIGTERM)
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr)
            self.assertNotIn("[done]", stderr)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()


if __name__ == "__main__":
    unittest.main()
