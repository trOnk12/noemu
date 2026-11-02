import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from bench import reproduce, agent_study


class BenchmarkTest(unittest.TestCase):
    def test_failed_study_action_cannot_return_expected_ui_text(self):
        failure = subprocess.CompletedProcess([], 1, "", "cannot tap Chips: driver disconnected")
        with mock.patch.object(agent_study.subprocess, "run", return_value=failure), self.assertRaises(RuntimeError):
            agent_study.noemu(Path("."), "tap", "MY CART")

    def test_study_does_not_use_stderr_as_ui_evidence(self):
        output = subprocess.CompletedProcess([], 0, "cart is empty", "debug: Chips")
        with mock.patch.object(agent_study.subprocess, "run", return_value=output):
            self.assertEqual(agent_study.noemu(Path("."), "screen"), "cart is empty")

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
