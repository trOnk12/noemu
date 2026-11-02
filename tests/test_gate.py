"""Gate control-flow regressions. Build/test programs are stubs, never Android evidence."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

ROOT = Path(__file__).resolve().parents[1]


class GateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.stubs = self.root / "stubs"
        self.results = self.root / "results"
        self.repo.mkdir()
        self.stubs.mkdir()
        (self.repo / "scripts").mkdir()
        for name in ("gate.sh", "gate_artifacts.py", "gate_command.py"):
            source = ROOT / "scripts" / name
            if source.exists():
                shutil.copyfile(source, self.repo / "scripts" / name)
        (self.repo / "fixtures").mkdir()
        (self.repo / "fixtures" / "marker").write_text("baseline")
        self.git = shutil.which("git")
        self.run_git("init", "-q")
        self.run_git("add", ".")
        self.run_git("-c", "user.name=Gate Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline")
        self.revision = self.run_git("rev-parse", "HEAD").strip()
        self.executable("git", f'''import os, sys
if os.environ.get("FAIL_CHECKOUT") and "checkout" in sys.argv:
    sys.exit(23)
if os.environ.get("IGNORE_CHECKOUT") and "checkout" in sys.argv:
    sys.exit(0)
os.execv({self.git!r}, [{self.git!r}, *sys.argv[1:]])
''')
        self.executable("java", "print('Java stub; no Android tests run')\n")
        self.executable("python3", f'''import os, pathlib, shutil, sys
if sys.argv[1:3] == ["-m", "venv"]:
    if os.environ.get("FAIL_VENV"):
        sys.exit(24)
    target = pathlib.Path(sys.argv[3]) / "bin"
    target.mkdir(parents=True)
    shutil.copyfile({str(self.stubs / 'worker')!r}, target / "python")
    (target / "python").chmod(0o755)
    (target / "noemu").write_text("#!/bin/sh\\nexit 0\\n")
    (target / "noemu").chmod(0o755)
elif len(sys.argv) > 1 and sys.argv[1].endswith(("gate_artifacts.py", "gate_command.py")):
    if os.environ.get("FAIL_ARTIFACTS") and sys.argv[1].endswith("gate_artifacts.py"):
        sys.exit(26)
    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
else:
    print("Python stub; no Android tests run")
''')
        self.executable("worker", f'''import json, os, pathlib, subprocess, sys
with open(os.environ["GATE_CALLS"], "a") as out:
    out.write(json.dumps({{"executable": sys.argv[0], "args": sys.argv[1:]}}) + "\\n")
if sys.argv[1:3] == ["-m", "unittest"]:
    sha = subprocess.check_output([{self.git!r}, "rev-parse", "HEAD"], text=True).strip()
    pathlib.Path(os.environ["GATE_TESTED_SHA"]).write_text(sha)
if sys.argv[1:3] == ["-m", "build"]:
    dist = pathlib.Path(sys.argv[3]) / "dist"
    dist.mkdir()
    (dist / "noemu-stub.whl").write_text("stub")
if len(sys.argv) > 1 and sys.argv[1].endswith("integration_driver.py"):
    src = pathlib.Path(sys.argv[1]).parents[1]
    log = src / "fixtures/.gradle/noemu-fleet/1/log.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("fixture startup diagnostic: " + os.environ["NOEMU_FIXTURE"])
    if os.environ.get("STALL_DRIVER") == os.environ["NOEMU_FIXTURE"]:
        import time
        pathlib.Path(os.environ["GATE_STALLED_PID"]).write_text(str(os.getpid()))
        time.sleep(30)
    if os.environ.get("FAIL_DRIVER") == os.environ["NOEMU_FIXTURE"]:
        print("startup failed; see " + str(log), file=sys.stderr)
        sys.exit(25)
''')
        self.executable("gradle", f'''import os, pathlib, shutil, sys
if sys.argv[1:2] == ["--version"]:
    print("Gradle stub; no Android tests run")
elif "-p" in sys.argv:
    wrapper = pathlib.Path(sys.argv[sys.argv.index("-p") + 1]) / "gradlew"
    shutil.copyfile({str(self.stubs / 'gradle')!r}, wrapper)
    wrapper.chmod(0o755)
else:
    module = next(a.split(":")[-1] for a in sys.argv if a.startswith("-Dnoemu.module="))
    report = pathlib.Path(module) / "build/test-results/testDebugUnitTest/TEST-protocol.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('<testsuite tests="5" failures="0"/>')
''')
        self.env = {**os.environ, "PATH": str(self.stubs) + os.pathsep + os.environ["PATH"],
                    "TMPDIR": str(self.root), "GATE_CALLS": str(self.root / "calls"),
                    "GATE_STALLED_PID": str(self.root / "stalled-pid"),
                    "GATE_TESTED_SHA": str(self.root / "tested-sha")}

    def executable(self, name, body):
        path = self.stubs / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def run_git(self, *args):
        return subprocess.check_output([self.git, "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def gate(self, revision=None, **env):
        return subprocess.run(["bash", str(self.repo / "scripts/gate.sh"), revision or self.revision,
                               str(self.results)], cwd=self.repo, env={**self.env, **env},
                              capture_output=True, text=True, timeout=15)

    def test_invalid_revision_stops_before_running_checks(self):
        result = self.gate("THIS_REVISION_DOES_NOT_EXIST")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("gate PASSED", result.stdout)
        self.assertFalse((self.root / "calls").exists())

    def test_checkout_failure_cannot_test_the_default_branch(self):
        result = self.gate(FAIL_CHECKOUT="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("gate PASSED", result.stdout)
        self.assertFalse((self.root / "calls").exists())

    def test_preparation_failure_stops_before_running_checks(self):
        result = self.gate(FAIL_VENV="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.results / "summary.txt").exists(), result.stdout)
        self.assertIn("status incomplete", (self.results / "result.txt").read_text())

    def test_successful_checkout_command_must_still_match_requested_head(self):
        (self.repo / "fixtures/marker").write_text("different revision")
        self.run_git("add", ".")
        self.run_git("-c", "user.name=Gate Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "later")
        result = self.gate(IGNORE_CHECKOUT="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.root / "calls").exists())
        self.assertIn("differs from requested", (self.results / "setup.log").read_text())

    def test_existing_results_are_not_overwritten(self):
        self.results.mkdir()
        evidence = self.results / "summary.txt"
        evidence.write_text("previous evidence")
        result = self.gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(evidence.read_text(), "previous evidence")

    def test_artifact_capture_failure_cannot_produce_a_passing_gate(self):
        result = self.gate(FAIL_ARTIFACTS="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("gate PASSED", result.stdout)
        self.assertIn("driver-compose", (self.results / "summary.txt").read_text())

    def test_gate_tests_the_requested_commit_and_keeps_success_artifacts(self):
        (self.repo / "fixtures/marker").write_text("later revision")
        self.run_git("add", ".")
        self.run_git("-c", "user.name=Gate Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "later")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "tested-sha").read_text(), self.revision)
        self.assertEqual((self.results / "result.txt").read_text(), f"revision {self.revision}\nstatus passed\n")
        calls = [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]
        drivers = [c for c in calls if any(a.endswith("integration_driver.py") for a in c["args"])]
        self.assertEqual(len(drivers), 2)
        self.assertTrue(all(c["executable"].endswith("/installed/bin/python") for c in drivers))
        reports = list(self.results.rglob("TEST-protocol.xml"))
        self.assertTrue(reports, "JUnit evidence was deleted")
        self.assertIn('failures="0"', reports[0].read_text())

    def test_driver_failure_keeps_logs_and_runs_remaining_checks(self):
        result = self.gate(FAIL_DRIVER="views")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("driver-compose", (self.results / "summary.txt").read_text())
        logs = list(self.results.rglob("log.txt"))
        self.assertTrue(any("diagnostic: views" in p.read_text() for p in logs), "Failed JVM log was deleted")
        self.assertTrue(any("diagnostic: compose" in p.read_text() for p in logs))

    def test_stalled_check_times_out_retains_evidence_and_continues(self):
        result = self.gate(STALL_DRIVER="views", NOEMU_GATE_CHECK_SECONDS="1",
                           NOEMU_GATE_KILL_GRACE_SECONDS="0.1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        summary = (self.results / "summary.txt").read_text()
        self.assertRegex(summary, r"driver-views\s+exit=124")
        self.assertIn("driver-compose", summary)
        self.assertIn("deadline exceeded", (self.results / "driver-views.log").read_text())
        self.assertTrue(any("diagnostic: views" in p.read_text() for p in self.results.rglob("log.txt")))

    def test_cancelling_gate_forwards_signal_and_keeps_run_incomplete(self):
        process = subprocess.Popen(["bash", str(self.repo / "scripts/gate.sh"), self.revision,
                                    str(self.results)], cwd=self.repo,
                                   env={**self.env, "STALL_DRIVER": "views", "NOEMU_GATE_KILL_GRACE_SECONDS": "0.1"},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            marker = self.root / "stalled-pid"
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "driver check did not start")
            child = psutil.Process(int(marker.read_text()))
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143)
            self.assertIn("status incomplete", (self.results / "result.txt").read_text())
            self.assertNotIn("driver-compose", (self.results / "summary.txt").read_text())
            try:
                self.assertIn(child.status(), (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD))
            except psutil.NoSuchProcess:
                pass
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
