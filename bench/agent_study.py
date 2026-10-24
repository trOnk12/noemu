"""Agent study: does running the app (noemu) help a coding agent in a sandbox without an emulator?

    python agent_study.py setup ROOT      # clean Jetsnack per task and arm, bugs injected
    python agent_study.py run ROOT        # claude -p per (task, arm); results.jsonl
    python agent_study.py check ROOT      # hidden noemu checks on each result; scores.jsonl

Arm A: Gradle + noemu. Arm B: Gradle only (what a cloud sandbox has). Same prompt otherwise.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# A checkout of github.com/android/compose-samples.
SAMPLES = Path(os.environ.get("COMPOSE_SAMPLES", "compose-samples"))
NOEMU = Path(__file__).resolve().parents[1] / "noemu" / "cli.py"
SRC = "app/src/main/java/com/example/jetsnack"

TASKS = {
    "T1": {"ask": "Show the number of items in the cart (the sum of the quantities) as a badge on the MY CART tab of "
                  "the bottom bar."},
    "T2": {"ask": "Add a 'Clear cart' button at the top of the cart screen. Tapping it empties the cart."},
    "T3": {"ask": "Bug report: searching for 'cup' shows no results, but there is a Cupcake. Fix it.",
           "inject": (f"{SRC}/model/Search.kt", "it.name.contains(query, ignoreCase = true)", "it.name.contains(query)")},
    "T4": {"ask": "Bug report: the cart's subtotal is wrong: it doesn't add up to the items in the cart. Fix it.",
           "inject": (f"{SRC}/ui/home/cart/Cart.kt", "orderLines.sumOf { it.snack.price * it.count }",
                      "orderLines.sumOf { it.snack.price }")},
    "T5": {"ask": "The ADD TO CART button on a snack's detail screen does nothing. Make it add that snack, with the "
                  "quantity chosen on that screen, to the cart, so it shows up on the MY CART tab."},
    "T6": {"ask": "Bug report: tapping a filter chip on the home screen (e.g. 'Organic') doesn't select it. Fix it.",
           "inject": (f"{SRC}/ui/components/Filters.kt", "onValueChange = setSelected,", "onValueChange = { setSelected(selected) },")},
}

BASE = ("You are working on this Android app (Jetpack Compose; the Gradle project is the current directory). Task: {ask} "
        "You are in a cloud sandbox without an emulator or a device; Gradle works (e.g. `./gradlew :app:compileDebugKotlin`). "
        "Make the change in the code. When you are done, reply with a short summary of what you changed and how you "
        "checked that it works.")
TOOL = (" noemu is installed: it runs the app on the JVM so you can see and use it, with no emulator: `noemu start` "
        "(builds and launches the app, ~1 minute), `noemu screen`, `noemu tap <number|label>`, `noemu type <number|label> "
        "<text>`, `noemu back`, `noemu scroll down`, `noemu stop`; each command prints the screen's elements and saves a "
        "screenshot you can look at. Restart it (`noemu stop`, `noemu start`) after changing code.")


def setup(root: Path) -> None:
    for task, spec in TASKS.items():
        for arm in ("A", "B"):
            d = root / arm / task
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            archive = subprocess.run(["git", "-C", str(SAMPLES), "archive", "HEAD", "Jetsnack"], capture_output=True, check=True).stdout
            subprocess.run(["tar", "-x", "--strip-components=1", "-C", str(d)], input=archive, check=True)
            wrapper = d / "gradle/wrapper/gradle-wrapper.properties"
            wrapper.write_text(wrapper.read_text().replace("gradle-9.5.0-bin", "gradle-9.5.1-bin"))
            if "inject" in spec:
                path, old, new = spec["inject"]
                f = d / path
                text = f.read_text()
                assert text.count(old) == 1, (task, old)
                f.write_text(text.replace(old, new))
            subprocess.run(["git", "init", "-q"], cwd=d)
            subprocess.run(["git", "add", "-A"], cwd=d)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start"], cwd=d)
    print("set up", len(TASKS), "tasks x 2 arms in", root)


def run(root: Path) -> None:
    out = root / "results.jsonl"
    done = {(r["arm"], r["task"]) for r in map(json.loads, out.read_text().splitlines())} if out.exists() else set()
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "noemu").write_text(f"#!/bin/sh\nexec python3 {NOEMU} \"$@\"\n")
    (bin_dir / "noemu").chmod(0o755)
    for task in TASKS:
        for arm in ("A", "B"):
            if (arm, task) in done:
                continue
            env = dict(os.environ)  # ANDROID_HOME, JAVA_HOME and NOEMU_GRADLE_ARGS come from the caller
            if arm == "A":
                env["PATH"] = f"{bin_dir}:{env['PATH']}"
            prompt = BASE.format(ask=TASKS[task]["ask"]) + (TOOL if arm == "A" else "")
            tools = "Read,Edit,Write,Grep,Glob,Bash(./gradlew:*),Bash(bash ./gradlew:*)" + (",Bash(noemu:*)" if arm == "A" else "")
            start = time.time()
            p = subprocess.run(["timeout", "3000", "claude", "-p", prompt, "--output-format", "json",
                                "--allowedTools", tools, "--max-turns", "80"],
                               cwd=root / arm / task, capture_output=True, text=True, env=env)
            subprocess.run(["python3", str(NOEMU), "stop"], cwd=root / arm / task, capture_output=True)
            try:
                res = json.loads(p.stdout)
            except ValueError:
                res = {"result": (p.stdout + p.stderr)[-2000:]}
            row = {"arm": arm, "task": task, "seconds": round(time.time() - start), "cost_usd": res.get("total_cost_usd"),
                   "turns": res.get("num_turns"), "answer": res.get("result"), "rc": p.returncode}
            if re.search(r"usage limit|session limit|rate limit", str(row["answer"]), re.I):
                print("usage limit reached; stopping", flush=True)
                raise SystemExit(3)
            with out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(arm, task, row["seconds"], row["cost_usd"], row["turns"], str(row["answer"])[-200:].replace("\n", " "), flush=True)


def noemu(d: Path, *args: str) -> str:
    env = dict(os.environ)
    p = subprocess.run(["python3", str(NOEMU), *args], cwd=d, capture_output=True, text=True, env=env, timeout=2400)
    return p.stdout + p.stderr


def hidden_check(task: str, d: Path) -> tuple[bool, str]:
    """The behaviour the task asks for, observed in the running app."""
    first = noemu(d, "start")
    if "app running" not in first:
        return False, "does not build or start: " + first[-300:]
    try:
        if task == "T1":
            ok = re.search(r"\b6\b", first) is not None and "MY CART" in first
            return ok, "home screen shows 6 near MY CART" if ok else "no 6 badge on home screen"
        if task == "T2":
            noemu(d, "tap", "MY CART")
            s = noemu(d, "tap", "Clear cart")
            ok = "Gingerbread" not in s and "Ice Cream Sandwich" not in s and "tapped" in s
            return ok, s[:300]
        if task == "T3":
            noemu(d, "tap", "SEARCH")
            s = noemu(d, "type", "2", "cup")
            s = noemu(d, "wait", "1500")
            return "Cupcake" in s, s[:300]
        if task == "T4":
            s = noemu(d, "tap", "MY CART")
            return "$54.44" in s, re.findall(r"\$[\d.]+", s)[:8].__str__()
        if task == "T5":
            noemu(d, "tap", "Chips")
            noemu(d, "tap", "ADD TO CART")
            noemu(d, "back")
            s = noemu(d, "tap", "MY CART")
            return "Chips" in s, s[:400]
        if task == "T6":
            s = noemu(d, "tap", "Organic")
            return bool(re.search(r"Organic \[(on|selected)\]", s)), s[:300]
    finally:
        noemu(d, "stop")
    return False, "unknown task"


def check(root: Path) -> None:
    out = root / "scores.jsonl"
    rows = [json.loads(l) for l in (root / "results.jsonl").read_text().splitlines()]
    for r in rows:
        ok, why = hidden_check(r["task"], root / r["arm"] / r["task"])
        with out.open("a") as f:
            f.write(json.dumps({"arm": r["arm"], "task": r["task"], "pass": ok, "why": why}) + "\n")
        print(r["arm"], r["task"], ok, why[:120].replace("\n", " "), flush=True)


if __name__ == "__main__":
    {"setup": setup, "run": run, "check": check}[sys.argv[1]](Path(sys.argv[2]))
