import fcntl
import json
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
RENDERER = [sys.executable, "-m", "nix_adapter.progress", "--renderer"]


def _pipes(process):
    stdin, stdout = process.stdin, process.stdout
    assert stdin is not None and stdout is not None
    return stdin, stdout


class ProgressTests(unittest.TestCase):
    def _pty_process(self, command=RENDERER, env=None, width=80):
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

    def test_renderer_retains_final_rows_and_handles_unknown_total(self):
        events = (
            b"\n".join(
                json.dumps(event).encode()
                for event in [
                    {"event": "start", "label": "compile", "total": 2},
                    {"event": "update", "completed": 1},
                    {"event": "finish", "status": "done"},
                    {"event": "start", "label": "wait", "total": None},
                    {"event": "finish", "status": "skip"},
                ]
            )
            + b"\n"
        )
        result = subprocess.run(RENDERER, input=events, capture_output=True, check=True)
        output = result.stderr.decode()
        self.assertIn("[done] compile", output)
        self.assertIn("[skip] wait", output)
        self.assertNotIn("\x1b[?1049h", output)
        self.assertNotIn("\x1b[2J", output)

    def test_suspend_ack_and_resume_do_not_consume_child_stdin(self):
        process = subprocess.Popen(
            RENDERER,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdin, stdout = _pipes(process)
        stdin.write(json.dumps({"event": "start", "label": "native"}).encode() + b"\n")
        stdin.write(json.dumps({"event": "suspend", "id": 7}).encode() + b"\n")
        stdin.flush()
        self.assertEqual(stdout.readline().strip(), b"7")
        stdin.write(json.dumps({"event": "resume"}).encode() + b"\n")
        stdin.write(json.dumps({"event": "finish", "status": "done"}).encode() + b"\n")
        stdin.close()
        self.assertEqual(process.wait(timeout=2), 0)
        stdout.close()
        assert process.stderr is not None
        process.stderr.close()

    def test_shell_bridge_keeps_native_output_and_returns_success_when_renderer_breaks(
        self,
    ):
        script = ROOT / "progress.sh"
        command = f"""
source {script}
        progress_renderer={shutil.which("false") or "false"}
progress_init test 0
progress_start "quoted label"
progress_suspend
printf 'native output\\n'
progress_resume
progress_finish done
progress_close
"""
        result = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout, "native output\n")

    def test_renderer_can_run_under_pty_without_alternate_screen(self):
        master, process = self._pty_process()
        stdin, stdout = _pipes(process)
        stdin.write(
            json.dumps(
                {"event": "start", "label": "resize", "total": 1, "id": 6}
            ).encode()
            + b"\n"
        )
        stdin.flush()
        self.assertEqual(stdout.readline().strip(), b"6")
        fcntl.ioctl(master, 0x5414, struct.pack("HHHH", 24, 24, 0, 0))
        time.sleep(0.15)
        stdin.write(
            json.dumps({"event": "finish", "status": "failed"}).encode() + b"\n"
        )
        stdin.close()
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0, output)
        self.assertIn("resize", output)
        self.assertNotIn("\x1b[?1049h", output)

    def test_verbose_term_dumb_and_empty_no_color_never_emit_ansi(self):
        for environment in (
            {"TERM": "xterm-256color"},
            {"TERM": "dumb"},
            {"NO_COLOR": ""},
        ):
            command = RENDERER + (
                ["--verbose"] if environment == {"TERM": "xterm-256color"} else []
            )
            master, process = self._pty_process(command, env=environment)
            stdin, _ = _pipes(process)
            stdin.write(
                json.dumps({"event": "start", "label": "plain", "total": 1}).encode()
                + b"\n"
            )
            stdin.write(
                json.dumps({"event": "finish", "status": "done"}).encode() + b"\n"
            )
            stdin.close()
            code, output = self._wait_pty(master, process)
            self.assertEqual(code, 0)
            self.assertNotIn(b"\x1b[", output, environment)
            self.assertIn(b"plain", output, environment)

    def test_unknown_task_refreshes_timer_without_explicit_updates(self):
        master, process = self._pty_process()
        stdin, stdout = _pipes(process)
        stdin.write(
            json.dumps({"event": "start", "label": "waiting", "id": 4}).encode() + b"\n"
        )
        stdin.flush()
        self.assertEqual(stdout.readline().strip(), b"4")
        time.sleep(0.25)
        stdin.write(json.dumps({"event": "finish", "status": "done"}).encode() + b"\n")
        stdin.close()
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
        master, process = self._pty_process(width=20)
        label = "編輯器-非常長的進度標籤"
        stdin, _ = _pipes(process)
        stdin.write(json.dumps({"event": "start", "label": label}).encode() + b"\n")
        stdin.write(json.dumps({"event": "finish", "status": "skip"}).encode() + b"\n")
        stdin.close()
        code, raw = self._wait_pty(master, process)
        output = raw.decode(errors="replace")
        self.assertEqual(code, 0, output)
        self.assertNotIn("Traceback", output)
        self.assertIn("↷", output)

    def test_ascii_terminal_uses_ascii_status_glyphs(self):
        master, process = self._pty_process(env={"PYTHONIOENCODING": "ascii"})
        stdin, _ = _pipes(process)
        for status in ("done", "skip", "failed"):
            stdin.write(
                json.dumps({"event": "start", "label": "ascii"}).encode() + b"\n"
            )
            stdin.write(
                json.dumps({"event": "finish", "status": status}).encode() + b"\n"
            )
        stdin.close()
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

    def test_renderer_eof_and_sigterm_exit_without_traceback(self):
        process = subprocess.Popen(
            RENDERER,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdin, stdout = _pipes(process)
        stdin.write(json.dumps({"event": "start", "label": "cleanup"}).encode() + b"\n")
        stdin.close()
        self.assertEqual(process.wait(timeout=2), 0)
        stdout.close()
        assert process.stderr is not None
        process.stderr.close()

        process = subprocess.Popen(
            RENDERER,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdin, stdout = _pipes(process)
        stdin.write(
            json.dumps({"event": "start", "label": "signal", "id": 9}).encode() + b"\n"
        )
        stdin.flush()
        self.assertEqual(stdout.readline().strip(), b"9")
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=2)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertNotIn(b"Traceback", stderr)

    def test_missing_and_stalled_renderers_are_bounded_and_successful(self):
        script = Path(__file__).parent / "progress.sh"
        with tempfile.TemporaryDirectory() as directory:
            stalled = Path(directory) / "stalled"
            sleep = shutil.which("sleep") or "sleep"
            stalled.write_text(f"#!/bin/sh\nexec {sleep} 10\n")
            stalled.chmod(0o755)
            command = f"""
source {script}
progress_renderer={stalled}
progress_base64=base64
progress_init test 0
progress_start stalled
progress_suspend
progress_close
unset progress_renderer
progress_init test 0
progress_start missing
progress_close
"""
            started = time.monotonic()
            result = subprocess.run(
                ["bash", "-c", command], capture_output=True, text=True, timeout=4
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(time.monotonic() - started, 4)
