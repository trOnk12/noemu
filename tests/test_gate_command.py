"""Real POSIX process tests for deadlines; no Android execution is implied."""
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

RUNNER = Path(__file__).resolve().parents[1] / "scripts/gate_command.py"


class GateCommandTest(unittest.TestCase):
    def command(self, code, seconds="2"):
        return [sys.executable, str(RUNNER), "--seconds", seconds, "--grace", "0.1",
                "--", sys.executable, "-c", code]

    def test_preserves_success_failure_and_output(self):
        for status in (0, 17):
            result = subprocess.run(self.command(f"print('diagnostic'); raise SystemExit({status})"),
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, status)
            self.assertIn("diagnostic", result.stdout)

    def test_invalid_deadlines_cannot_execute_command(self):
        for seconds in ("0", "-1", "nan", "inf", "86401", "invalid"):
            result = subprocess.run(self.command("print('EXECUTED')", seconds),
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("EXECUTED", result.stdout)

    def test_missing_executable_is_explicit_failure(self):
        result = subprocess.run([sys.executable, str(RUNNER), "--seconds", "1", "--",
                                 "/noemu-nonexistent-executable"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 125)
        self.assertIn("could not run", result.stderr)

    def assert_stopped(self, pid):
        try:
            self.assertIn(psutil.Process(pid).status(), (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD))
        except psutil.NoSuchProcess:
            pass

    def process_tree(self, interrupt):
        with tempfile.TemporaryDirectory() as temp:
            ready = Path(temp) / "ready"
            # Child ignores TERM; parent exits on TERM. Both must stop before return.
            child = ("import os, signal, time; from pathlib import Path; "
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                     f"Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)")
            parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
            process = subprocess.Popen(self.command(parent, "10" if interrupt else "1"),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child_pid = None
            child_process = None
            try:
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "child did not start")
                child_pid = int(ready.read_text())
                child_process = psutil.Process(child_pid)
                parent_pid = child_process.ppid()
                if interrupt:
                    process.send_signal(signal.SIGTERM)
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 143 if interrupt else 124)
                self.assertIn("interrupted" if interrupt else "deadline exceeded", stderr)
                self.assert_stopped(parent_pid)
                self.assert_stopped(child_pid)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)
                if child_process:
                    try:
                        child_process.kill()
                    except psutil.NoSuchProcess:
                        pass

    def test_timeout_stops_term_resistant_descendant(self):
        self.process_tree(False)

    def test_cancellation_stops_term_resistant_descendant(self):
        self.process_tree(True)


if __name__ == "__main__":
    unittest.main()
