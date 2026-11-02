#!/usr/bin/env python3
"""Runs one journey against a broken app and two candidate fixes, each in its own git worktree and generation.

    NOEMU_HOME=$(mktemp -d) python examples/rotation-fixes/compare.py [--out report.json]

Every variant replaces CounterActivity in the Views fixture, is built into its own immutable generation, and runs on its
own exclusive instance, concurrently within the fleet's limits. The journey checks the counter after ordinary use, after
rotation, and after one more tap. The result is a JVM simulation on Robolectric; it is not verified on Android.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = "fixtures/shared-main/dev/noemu/fixture/CounterActivity.kt"
VARIANTS = ("broken", "candidate-a", "candidate-b")
JOURNEY = [  # (action, arguments, expected counter after it)
    ("open", ["dev.noemu.fixture.CounterActivity"], 0),
    ("tap", ["Add"], 1),
    ("tap", ["Add"], 2),
    ("tap", ["Add"], 3),
    ("rotate", [], 3),
    ("tap", ["Add"], 4),
]


def noemu(cwd: Path, *args, lease=None, timeout=900):
    env = {**os.environ, "NOEMU_SHOTS": "0"}
    env.pop("NOEMU_LEASE", None)
    if lease:
        env["NOEMU_LEASE"] = lease
    return subprocess.run([sys.executable, "-m", "noemu", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=env)


def prepare(base: Path, variant: str) -> Path:
    tree = base / variant
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(tree), "HEAD"], cwd=ROOT, check=True)
    for generated in ("gradlew", "gradle"):  # Generated once and ignored by git; see fixtures/README.md.
        source = ROOT / "fixtures" / generated
        if source.is_dir():
            shutil.copytree(source, tree / "fixtures" / generated)
        else:
            shutil.copy2(source, tree / "fixtures" / generated)
    shutil.copy(HERE / f"{variant}.kt", tree / TARGET)
    return tree


def counter(screen: str) -> int | None:
    found = re.search(r"Count: (\d+)", screen)
    return int(found.group(1)) if found else None


def build(variant: str, tree: Path, report: dict) -> bool:
    entry = report[variant] = {"steps": [], "verdict": "incomplete"}
    started = time.perf_counter()
    built = noemu(tree / "fixtures", "build", "--module", ":views")
    if built.returncode:
        entry["error"] = "build failed: " + built.stderr.strip()[-400:]
        return False
    entry["build_seconds"] = round(time.perf_counter() - started, 1)
    return True


def run(variant: str, tree: Path, report: dict) -> None:
    project = tree / "fixtures"
    entry = report[variant]
    granted = noemu(project, "acquire", "--json", "--wait", "600")
    if granted.returncode:
        entry["error"] = granted.stderr.strip()
        return
    grant = json.loads(granted.stdout)
    entry.update(instance=grant["instance"], generation=grant["generation"])
    try:
        for action, arguments, expected in JOURNEY:
            result = noemu(project, action, *arguments, lease=grant["lease"], timeout=180)
            seen = counter(result.stdout) if result.returncode == 0 else None
            step = {"action": " ".join([action, *arguments]), "expected": expected, "observed": seen,
                    "passed": result.returncode == 0 and seen == expected}
            if result.returncode:
                step["error"] = result.stderr.strip()
            entry["steps"].append(step)
            if not step["passed"]:
                entry["verdict"] = "failed"
                entry["first_failure"] = step
                return
        entry["verdict"] = "passed"
    finally:
        noemu(project, "release", lease=grant["lease"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="rotation-fixes-report.json")
    args = parser.parse_args()
    if not os.environ.get("NOEMU_HOME"):
        sys.exit("set NOEMU_HOME to a private directory, for example NOEMU_HOME=$(mktemp -d)")
    if not (ROOT / "fixtures/gradlew").exists():
        sys.exit("generate the fixture wrapper first; see fixtures/README.md")
    base = Path(tempfile.mkdtemp(prefix="noemu-candidates-"))
    trees = {}
    report: dict = {}
    try:
        trees = {variant: prepare(base, variant) for variant in VARIANTS}
        # Builds one at a time (each Gradle build takes gigabytes); the journeys run concurrently.
        built = [variant for variant, tree in trees.items() if build(variant, tree, report)]
        threads = [threading.Thread(target=run, args=(variant, trees[variant], report)) for variant in built]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        for tree in trees.values():
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT, check=False)
        shutil.rmtree(base, ignore_errors=True)
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(f"{'variant':<13} {'generation':<17} {'verdict':<10} first failure")
    for variant in VARIANTS:
        entry = report.get(variant, {})
        failure = entry.get("first_failure")
        detail = (f"after `{failure['action']}`: expected {failure['expected']}, saw {failure['observed']}" if failure
                  else entry.get("error", ""))
        print(f"{variant:<13} {entry.get('generation', '-'):<17} {entry.get('verdict', 'incomplete'):<10} {detail}")
    print("JVM simulation on Robolectric; not verified on Android.")
    return 0 if all(report.get(v, {}).get("verdict") in ("passed", "failed") for v in VARIANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
