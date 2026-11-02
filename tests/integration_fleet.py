"""Milestone B's live exit gate: real builds, real JVMs, two real git worktrees.

Requires NOEMU_HOME pointing at a private directory, and the toolchain of fixtures/README.md. Uses the Views fixture. It
asserts that concurrent agents across worktrees cannot share an exclusive instance, exceed configured limits, cross
instance data boundaries, or run a different generation accidentally.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = "fixtures/views/src/main/java/dev/noemu/fixture/views/MainActivity.kt"
STORAGE = "dev.noemu.fixture.StorageActivity"


def noemu(project: Path, *args, lease=None, timeout=600, check=True):
    env = {**os.environ, "NOEMU_SHOTS": "0"}
    env.pop("NOEMU_LEASE", None)
    if lease:
        env["NOEMU_LEASE"] = lease
    result = subprocess.run([sys.executable, "-m", "noemu", *args], cwd=project, capture_output=True, text=True,
                            timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(f"noemu {args}: {result.stdout}\n{result.stderr}")
    return result


def acquire(project: Path, *args) -> dict:
    return json.loads(noemu(project, "acquire", "--json", *args).stdout)


def status(project: Path) -> dict:
    return json.loads(noemu(project, "status", "--json").stdout)


class WorktreeFleetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("NOEMU_HOME"):
            raise RuntimeError("set NOEMU_HOME to a private directory; this test must not touch a real fleet")
        cls.base = Path(tempfile.mkdtemp(prefix="noemu-worktrees-"))
        cls.worktrees = []
        cls.addClassCleanup(cls.remove_worktrees)  # Runs even when setup fails part way.
        cls.first, cls.second = cls.worktree("first"), cls.worktree("second")
        cls.build(cls.first)
        cls.build(cls.second)

    @classmethod
    def remove_worktrees(cls):
        for tree in cls.worktrees:
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT, check=False)
        shutil.rmtree(cls.base, ignore_errors=True)

    @classmethod
    def worktree(cls, name: str) -> Path:
        tree = cls.base / name
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(tree), "HEAD"], cwd=ROOT, check=True)
        cls.worktrees.append(tree)
        for generated in ("gradlew", "gradlew.bat", "gradle"):  # Generated once and ignored by git.
            source = ROOT / "fixtures" / generated
            if source.is_dir():
                shutil.copytree(source, tree / "fixtures" / generated)
            elif source.exists():
                shutil.copy2(source, tree / "fixtures" / generated)
        cls.label(tree, f"Branch {name}")
        return tree

    @classmethod
    def label(cls, tree: Path, text: str) -> None:
        """Gives a worktree's app its own visible code."""
        path = tree / ACTIVITY
        source = path.read_text()
        source = re.sub(r'text = "(ready|Branch [^"]*)"', f'text = "{text}"', source, count=1)
        path.write_text(source)

    @classmethod
    def build(cls, tree: Path) -> str:
        output = noemu(tree / "fixtures", "build", "--module", ":views", timeout=900).stdout
        return re.search(r"generation (\w+)", output).group(1)

    def project(self, tree: Path) -> Path:
        return tree / "fixtures"

    def release_later(self, tree, grant):
        self.addCleanup(lambda: noemu(self.project(tree), "release", lease=grant["lease"], check=False))

    def lease_on(self, tree: Path) -> dict:
        grant = acquire(self.project(tree))
        self.release_later(tree, grant)
        return grant

    def act(self, tree: Path, grant: dict, *command, check=True):
        return noemu(self.project(tree), *command, lease=grant["lease"], check=check, timeout=150)

    def screen(self, tree: Path, grant: dict) -> str:
        return self.act(tree, grant, "screen").stdout

    def identity(self, tree: Path, grant: dict) -> str:
        code = ("import sys; from noemu import cli; from noemu.protocol import Command;"
                f"cli.LEASE={grant['lease']!r}; print(cli.request(Command('screen')).data['identity']['build'])")
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=150).stdout.strip()

    def test_concurrent_agents_across_worktrees_get_their_own_code_and_instance(self):
        grants = {}
        def agent(tree):
            grants[tree] = self.lease_on(tree)
        threads = [threading.Thread(target=agent, args=(tree,)) for tree in (self.first, self.second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        first, second = grants[self.first], grants[self.second]
        self.assertNotEqual(first["instance"], second["instance"])
        self.assertNotEqual(first["generation"], second["generation"])
        self.assertIn("Branch first", self.screen(self.first, first))
        self.assertIn("Branch second", self.screen(self.second, second))
        self.assertEqual(self.identity(self.first, first), first["generation"])
        self.assertEqual(self.identity(self.second, second), second["generation"])
        refused = noemu(self.project(self.second), "--id", first["instance"], "screen", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("lease_required", refused.stderr)

    def test_limits_hold_and_a_third_agent_is_told_why_it_waits(self):
        self.lease_on(self.first), self.lease_on(self.second)
        refused = noemu(self.project(self.first), "acquire", "--wait", "1", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("capacity_timeout", refused.stderr)
        self.assertIn("2/2 active", refused.stderr)
        report = status(self.first)
        live = [i for i in report["instances"] if i["state"] != "failed"]
        self.assertLessEqual(len(live), report["limits"]["max_jvms"])
        self.assertLessEqual(report["active"], report["limits"]["max_active"])
        self.assertLessEqual(report["memory"]["charged_mib"], report["memory"]["budget_mib"])

    def test_instances_cannot_see_each_other_s_storage_and_replacements_start_clean(self):
        first, second = self.lease_on(self.first), self.lease_on(self.first)
        self.act(self.first, first, "open", STORAGE)
        stored = self.act(self.first, first, "tap", "Store token").stdout
        self.assertIn("file=token prefs=token relative=token", stored)
        other = self.act(self.first, second, "open", STORAGE).stdout
        self.assertIn("file=none prefs=none relative=none", other)
        self.assertFalse((self.project(self.first) / "relative-token").exists())
        self.assertFalse((self.project(self.first) / "views/relative-token").exists())
        noemu(self.project(self.first), "release", lease=first["lease"])
        replacement = self.lease_on(self.first)
        self.assertNotEqual(replacement["instance"], first["instance"])
        self.assertIn("file=none prefs=none relative=none", self.act(self.first, replacement, "open", STORAGE).stdout)

    def test_builds_never_change_a_running_instance(self):
        tree = self.worktree("rebuilt")
        old = self.build(tree)
        grant = self.lease_on(tree)
        self.assertIn("Branch rebuilt", self.screen(tree, grant))

        self.label(tree, "Branch newer")
        new = self.build(tree)
        self.assertNotEqual(old, new)
        self.assertIn("Branch rebuilt", self.screen(tree, grant))

        path = tree / ACTIVITY
        working = path.read_text()
        path.write_text(working + "\nthis does not compile\n")
        failed = noemu(self.project(tree), "build", "--module", ":views", check=False, timeout=900)
        self.assertNotEqual(failed.returncode, 0)
        path.write_text(working)
        self.assertIn("Branch rebuilt", self.screen(tree, grant))
        self.assertEqual(self.identity(tree, grant), old)

        shutil.rmtree(self.project(tree) / "views/build")
        self.assertIn("Branch rebuilt", self.act(tree, grant, "rotate").stdout)
        latest = self.lease_on(tree)
        self.assertEqual(latest["generation"], new, "the current generation runs with no build directory left")
        self.assertIn("Branch newer", self.screen(tree, latest))
        self.assertEqual(self.identity(tree, latest), new)

    def test_every_process_is_gone_after_release(self):
        grant = self.lease_on(self.second)
        pid = int((Path(os.environ["NOEMU_HOME"]) / "instances" / grant["instance"] / "pid").read_text())
        noemu(self.project(self.second), "release", lease=grant["lease"])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
