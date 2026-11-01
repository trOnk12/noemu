import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from bench import reproduce


class BenchmarkTest(unittest.TestCase):
    def test_failed_rotation_cannot_be_counted_as_lost_text(self):
        outputs = [subprocess.CompletedProcess([], 0, "[0] field cupcake", ""),
                   subprocess.CompletedProcess([], 0, "[0] field cupcake", ""),
                   subprocess.CompletedProcess([], 1, "", "noemu: execution_error: rotation crashed")]
        session = reproduce.Session(Path("."), io.StringIO())
        with mock.patch.object(reproduce.subprocess, "run", side_effect=outputs), self.assertRaises(RuntimeError):
            reproduce.search_lost_on_rotation(session)

    def test_expected_crash_requires_failure_and_specific_evidence(self):
        session = reproduce.Session(Path("."), io.StringIO())
        for code, out in ((0, "was already used"), (1, "connection refused")):
            with mock.patch.object(reproduce.subprocess, "run", return_value=subprocess.CompletedProcess([], code, out, "")), \
                    self.assertRaises(AssertionError):
                session("tap", "Send", expected_error="was already used")
        with mock.patch.object(reproduce.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "was already used", "")):
            self.assertIn("was already used", session("tap", "Send", expected_error="was already used"))
