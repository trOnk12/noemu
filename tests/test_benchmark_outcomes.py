"""Benchmark control flow with injected commands; these do not run apps or models."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from bench import agent_study, commands, crawl_corpus


def completed(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class StudyOutcomesTest(unittest.TestCase):
    def study(self, outputs, task="T4"):
        with mock.patch.object(commands.subprocess, "run", side_effect=outputs) as run:
            result = agent_study.hidden_check(task, Path("."))
        return result, run

    def test_failed_start_with_success_marker_cannot_pass(self):
        result, run = self.study([completed(1, "app running MY CART 6", "screen failed"), completed()], "T1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["returncode"], 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-1], "stop")

    def test_failed_action_and_cleanup_preserve_both_errors(self):
        result, _ = self.study([completed(stdout="app running"), completed(7, "$54.44", "tap failed"),
                                completed(8, stderr="stop failed")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["returncode"], 7)
        self.assertEqual(result["cleanup_error"]["returncode"], 8)

    def test_timeout_preserves_partial_output_and_is_inconclusive(self):
        timeout = subprocess.TimeoutExpired(["noemu", "start"], 2400, output=b"partial start", stderr=b"pending")
        result, _ = self.study([timeout, completed()])
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["error"]["stdout"], "partial start")
        self.assertEqual(result["error"]["stderr"], "pending")

    def test_cleanup_failure_prevents_overall_pass(self):
        result, _ = self.study([completed(), completed(stdout="$54.44"), completed(1)])
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["assertion_status"], "passed")

    def test_successful_execution_still_requires_the_assertion(self):
        for screen, status in (("$54.44", "passed"), ("$1.00", "failed")):
            with self.subTest(screen=screen):
                result, _ = self.study([completed(), completed(stdout=screen), completed()])
                self.assertEqual(result["status"], status)
                self.assertEqual(result["kind"], "assertion")

    def test_unknown_task_does_not_start_or_stop_an_app(self):
        result, run = self.study([], "unknown")
        self.assertEqual(result["status"], "unsupported")
        run.assert_not_called()

    def test_commands_use_installed_module_and_ignore_selected_fleet_instance(self):
        with mock.patch.dict(os.environ, {"NOEMU_ID": "123"}), \
                mock.patch.object(commands.subprocess, "run", return_value=completed()) as run:
            agent_study.noemu(Path("."), "stop")
        self.assertEqual(run.call_args.args[0], [*commands.NOEMU, "stop"])
        self.assertNotIn("NOEMU_ID", run.call_args.kwargs["env"])

    def test_scoring_continues_after_inconclusive_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "results.jsonl").write_text('\n'.join(json.dumps({"arm": arm, "task": "T4"}) for arm in ("A", "B")))
            outputs = [OSError("cannot launch"), completed(), completed(), completed(stdout="$54.44"), completed()]
            with mock.patch.object(commands.subprocess, "run", side_effect=outputs), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(agent_study.check(root), 1)
            rows = [json.loads(line) for line in (root / "scores.jsonl").read_text().splitlines()]
            self.assertEqual([r["status"] for r in rows], ["inconclusive", "passed"])
            self.assertEqual([r["pass"] for r in rows], [False, True])
            self.assertTrue(all(r["version"] == 2 for r in rows))


class CorpusOutcomesTest(unittest.TestCase):
    def corpus(self, outputs, expected_code=1):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sequence = iter(outputs)

            def execute(command, **kwargs):
                result = next(sequence)
                if isinstance(result, BaseException):
                    raise result
                if command[:2] == ["git", "clone"] and result.returncode == 0:
                    Path(command[-1]).mkdir(parents=True)
                return result

            with mock.patch.object(crawl_corpus, "APPS", [("app", "https://example.invalid/repo", "testDebugUnitTest", "")]), \
                    mock.patch.object(commands.subprocess, "run", side_effect=execute) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl_corpus.run(root, 5), expected_code)
            return json.loads((root / "app.txt").read_text().splitlines()[0]), run.call_args_list

    def test_clone_failure_records_failure_without_attempting_app(self):
        result, calls = self.corpus([completed(128, stderr="clone failed")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "clone")
        self.assertEqual(len(calls), 1)

    def test_success_records_only_command_execution_scope(self):
        result, _ = self.corpus([completed(), completed(stdout="a" * 40), completed(),
                                 completed(stdout="0 problems"), completed()], expected_code=0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["scope"], "command_execution")
        self.assertEqual(result["commit"], "a" * 40)

    def test_start_failure_cannot_be_overridden_by_success_marker(self):
        result, calls = self.corpus([completed(), completed(stdout="a" * 40),
                                     completed(1, "app running", "crashed"), completed()])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "start")
        self.assertEqual(calls[-1].args[0][-1], "stop")
        self.assertFalse(any("crawl" in c.args[0] for c in calls))

    def test_crawl_failure_retains_its_diagnostics(self):
        result, _ = self.corpus([completed(), completed(stdout="a" * 40), completed(),
                                 completed(1, "0 problems", "driver disconnected"), completed()])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "crawl")
        self.assertEqual(result["error"]["stderr"], "driver disconnected")

    def test_cleanup_timeout_prevents_overall_pass(self):
        result, _ = self.corpus([completed(), completed(stdout="a" * 40), completed(),
                                 completed(stdout="0 problems"), subprocess.TimeoutExpired(["stop"], 120)])
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["scope"], "command_execution")
        self.assertIn("cleanup_error", result)

    def test_historical_report_does_not_become_a_new_passing_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "app.txt"
            report.write_text(json.dumps({"summary": "0 problems"}) + "\n")
            with mock.patch.object(crawl_corpus, "APPS", [("app", "url", "task", "")]), \
                    mock.patch.object(commands.subprocess, "run") as run:
                self.assertEqual(crawl_corpus.run(root, 5), 1)
            run.assert_not_called()
            self.assertEqual(json.loads(report.read_text()), {"summary": "0 problems"})

    def test_existing_workspace_is_preserved_without_running_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            previous = root / "src/app/log.txt"
            previous.parent.mkdir(parents=True)
            previous.write_text("previous diagnostics")
            with mock.patch.object(crawl_corpus, "APPS", [("app", "url", "task", "")]), \
                    mock.patch.object(commands.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                crawl_corpus.run(root, 5)
            run.assert_not_called()
            self.assertEqual(previous.read_text(), "previous diagnostics")
            self.assertEqual(json.loads((root / "app.txt").read_text().splitlines()[0])["status"], "inconclusive")


class ProbeOutcomesTest(unittest.TestCase):
    def test_probe_diagnostics_do_not_hide_gradle_failure(self):
        script = Path(__file__).resolve().parents[1] / "bench/probe/probe.sh"
        for exit_code in (0, 17):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "gradlew").write_text("mkdir -p app/build/test-results/testDebugUnitTest\n"
                    "echo 'PROBE diagnostic' > app/build/test-results/testDebugUnitTest/TEST-dev.noemu.Probe.xml\n"
                    f"exit {exit_code}\n")
                result = subprocess.run(["bash", str(script)], cwd=root, capture_output=True, text=True, timeout=10)
                self.assertIn("PROBE diagnostic", result.stdout)
                self.assertEqual(result.returncode, exit_code)
