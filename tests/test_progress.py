import fcntl
import os
import pty
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent


def _pipes(process):
    stdin, stdout = process.stdin, process.stdout
    assert stdin is not None and stdout is not None
    return stdin, stdout


def script(body):
    """A child process that drives Progress the way an adapter does."""
    return [
        sys.executable,
        "-c",
        "import time\nfrom nix_adapter import Progress\n" + body,
    ]


class ProgressTests(unittest.TestCase):
    def _pty_process(self, command, env=None, width=80):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, 0x5414, struct.pack("HHHH", 24, width, 0, 0))
        child_env = os.environ.copy()
        child_env["TERM"] = "xterm-256color"
        child_env.pop("NO_COLOR", None)
        child_env.pop("CI", None)
        if env:
            child_env.update(env)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=slave,
            env=child_env,
        )
        os.close(slave)
        return master, process

    def _wait_pty(self, master, process):
        code = process.wait(timeout=2)
        chunks = []
        try:
            while True:
                chunk = os.read(master, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            pass
        output = b"".join(chunks)
        os.close(master)
        stdin, stdout = _pipes(process)
        stdin.close()
        stdout.close()
        return code, output

    def test_plain_log_keeps_final_rows_and_unknown_totals(self):
        result = subprocess.run(
            script(
                "with Progress('x') as p:\n"
                "    with p.task('compile', total=2) as t:\n"
                "        t.update(1)\n"
                "    p.start('wait').finish('skip')\n"
            ),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("[done] compile", result.stderr)
        self.assertIn("[skip] wait", result.stderr)
        self.assertNotIn("\x1b[", result.stderr)

    def test_external_output_reaches_the_terminal_untouched(self):
        result = subprocess.run(
            script(
                "import subprocess, sys\n"
                "with Progress('x') as p:\n"
                "    with p.task('native') as t, t.external_output():\n"
                "        subprocess.run([sys.executable, '-c', 'print(\\'native output\\')'])\n"
            ),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "native output\n")

    def test_terminal_display_avoids_the_alternate_screen(self):
        master, process = self._pty_process(
            script(
                "with Progress('x') as p:\n"
                "    t = p.start('resize', total=1)\n"
                "    time.sleep(0.15)\n"
                "    t.finish('failed')\n"
            )
        )
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0, output)
        self.assertIn("resize", output)
        self.assertNotIn("\x1b[?1049h", output)

    def test_verbose_term_dumb_and_empty_no_color_never_emit_ansi(self):
        for environment, verbose in (
            ({"TERM": "xterm-256color"}, True),
            ({"TERM": "dumb"}, False),
            ({"NO_COLOR": ""}, False),
        ):
            master, process = self._pty_process(
                script(
                    f"with Progress('x', verbose={verbose}) as p:\n"
                    "    with p.task('plain', total=1):\n"
                    "        pass\n"
                ),
                env=environment,
            )
            code, output = self._wait_pty(master, process)
            self.assertEqual(code, 0)
            self.assertNotIn(b"\x1b[", output, environment)
            self.assertIn(b"plain", output, environment)

    def test_unknown_task_refreshes_timer_without_explicit_updates(self):
        master, process = self._pty_process(
            script(
                "with Progress('x') as p:\n"
                "    with p.task('waiting'):\n"
                "        time.sleep(0.25)\n"
            )
        )
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0)
        elapsed = re.findall(r"\d+\.\ds", output)
        self.assertGreaterEqual(len(set(elapsed)), 2, output)

    def test_nested_tasks_retain_one_final_row_each(self):
        code = """
from nix_adapter import Progress
with Progress('nested') as progress:
    with progress.task('parent'):
        with progress.task('child'):
            pass
"""
        master, process = self._pty_process([sys.executable, "-c", code])
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(re.findall(r"✓[^\n]*child", output)), 1, output)
        self.assertEqual(len(re.findall(r"✓[^\n]*parent", output)), 1, output)

    def test_unicode_label_is_cropped_to_narrow_terminal(self):
        master, process = self._pty_process(
            script(
                "with Progress('x') as p:\n"
                "    p.start('編輯器-非常長的進度標籤').finish('skip')\n"
            ),
            width=20,
        )
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0, output)
        self.assertNotIn("Traceback", output)
        self.assertIn("↷", output)

    def test_ascii_terminal_uses_ascii_status_glyphs(self):
        master, process = self._pty_process(
            script(
                "with Progress('x') as p:\n"
                "    for status in ('done', 'skip', 'failed'):\n"
                "        p.start('ascii').finish(status)\n"
            ),
            env={"PYTHONIOENCODING": "ascii"},
        )
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0, output)
        self.assertIn("+", output)
        self.assertIn("-", output)
        self.assertIn("!", output)
        self.assertNotIn("Traceback", output)

    def test_business_exception_survives_broken_ui_stream(self):
        code = """
import sys
from nix_adapter import Progress
class Broken:
    def isatty(self): return False
    def write(self, value): raise OSError('renderer closed')
    def flush(self): raise OSError('renderer closed')
real = sys.__stderr__
sys.stderr = Broken()
try:
    with Progress('broken') as progress:
        with progress.task('work'):
            raise ValueError('business failure')
except Exception as error:
    sys.stderr = real
    real.write(type(error).__name__ + ':' + str(error) + '\\n')
"""
        for probe in ("return False", "raise OSError('terminal closed')"):
            with self.subTest(isatty=probe):
                result = subprocess.run(
                    [sys.executable, "-c", code.replace("return False", probe)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ValueError:business failure", result.stderr)
