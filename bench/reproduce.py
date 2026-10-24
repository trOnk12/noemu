"""Re-checks every bug noemu claims to find, from fresh clones at fixed commits, and prints PASS or FAIL for each.

    pipx install git+https://github.com/trOnk12/noemu
    python bench/reproduce.py WORKDIR [CASE ...]

Each case clones an app at a pinned commit (and, for a "put back" case, re-applies the historical bug as the fix
commit undoes it), drives it with noemu commands written out below, and checks the output. Nothing is crawled at
random: the same steps give the same result on every run. Logs go to WORKDIR/logs/CASE.txt. Needs a JDK 17+,
the Android SDK (ANDROID_HOME) and network for Gradle; NOEMU_GRADLE_ARGS is passed through.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

NOEMU = [shutil.which("noemu")] if shutil.which("noemu") else [sys.executable, "-m", "noemu"]


@dataclass
class Case:
    name: str
    claim: str
    repo: str
    commit: str
    project: str = "."                     # the Gradle project inside the clone
    task: str = "testDebugUnitTest"
    start: list[str] = field(default_factory=list)
    patch: list[tuple[str, str, str]] = field(default_factory=list)  # (file, old, new): exact text, applied once
    run: Callable[["Session"], str] | None = None  # drives the app, returns "" when the claim holds, else why not


class Session:
    def __init__(self, project: Path, log):
        self.project, self.log = project, log

    def __call__(self, *args: str, timeout: int = 900) -> str:
        p = subprocess.run([*NOEMU, *args], cwd=self.project, capture_output=True, text=True, timeout=timeout)
        out = p.stdout + p.stderr
        self.log.write(f"$ noemu {' '.join(args)}\n{out}\n")
        self.log.flush()
        return out

    def field(self, out: str) -> str:
        """Number of the first text field on the screen."""
        m = re.search(r"^\[(\d+)\] field ", out, re.M)
        if not m:
            raise AssertionError("no text field on screen")
        return m.group(1)


def elements(out: str) -> int:
    return len(re.findall(r"^\[\d+\] ", out, re.M))


# ------------------------------------------------------------------------------------------------------ the claims
def jetchat_same_message_twice(s: Session) -> str:
    for _ in range(2):
        s("type", "Text input", "ok")
        out = s("tap", "Send")
    return "" if "was already used" in out else "no crash on the second Send"


def jetsnack_search_rotation(s: Session) -> str:
    out = s("tap", "SEARCH")
    out = s("type", s.field(out), "cupcake")
    if "field cupcake" not in out:
        return "typing did not reach the search field"
    out = s("rotate", "2")
    return "" if "field cupcake" not in out else "the query survived the rotation"


def calendar_stacks_on_rotation(s: Session) -> str:
    before = elements(s("screen"))
    after = elements(s("rotate", "2"))
    return "" if after > before * 1.5 else f"{before} elements before, {after} after: no stacking"


def calendar_does_not_stack(s: Session) -> str:
    before = elements(s("screen"))
    after = elements(s("rotate", "2"))
    return "" if after == before else f"{before} elements before, {after} after"


def editor_text_lost_on_rotation(s: Session) -> str:
    out = s("type", s.field(s("screen")), "my unsaved edit")
    if "field my unsaved edit" not in out:
        return "typing did not reach the editor"
    out = s("rotate", "2")
    return "" if "field my unsaved edit" not in out else "the edit survived the rotation"


def editor_text_kept_on_rotation(s: Session) -> str:
    s("type", s.field(s("screen")), "my unsaved edit")
    out = s("rotate", "2")
    return "" if "field my unsaved edit" in out else "the edit was lost"


def leak_with_chain(s: Session) -> str:
    s("rotate", "2")
    out = s("leaks")
    app = [b for b in out.split("\n\n") if "still in memory after" in b and "(not the app's)" not in b]
    return "" if any("ThemeChanges.listeners" in b for b in app) else "no app leak through ThemeChanges.listeners"


def no_app_leak(s: Session) -> str:
    s("rotate", "2")
    out = s("leaks")
    app = [b for b in out.split("\n\n") if "still in memory after" in b and "(not the app's)" not in b]
    return "" if not app else "reported: " + app[0][:200]


LISTENER_LEAK = [(
    "app/src/main/kotlin/org/fossify/math/activities/MainActivity.kt",
    "        super.onCreate(savedInstanceState)\n",
    "        super.onCreate(savedInstanceState)\n        ThemeChanges.listeners += { refreshMenuItems() }  // registered, never removed\n",
), (
    "app/src/main/kotlin/org/fossify/math/activities/MainActivity.kt",
    "\nclass MainActivity",
    "\n/** An app-wide event source, as many apps have one. */\nobject ThemeChanges {\n    val listeners = mutableListOf<() -> Unit>()\n}\n\nclass MainActivity",
)]

FILE_MANAGER_111 = [(  # File Manager #111 undone: the editor saves and restores nothing
    "app/src/main/kotlin/org/fossify/filemanager/activities/ReadTextActivity.kt",
    "        if (originalText != binding.readTextView.text.toString()) {\n"
    "            outState.putString(KEY_UNSAVED_TEXT, binding.readTextView.text.toString())\n        }\n",
    "",
), (
    "app/src/main/kotlin/org/fossify/filemanager/activities/ReadTextActivity.kt",
    "\n            if (savedInstanceState != null) {\n"
    "                textToSet = savedInstanceState.getString(KEY_UNSAVED_TEXT, originalText)\n            }\n",
    "",
)]

SAMPLES = "https://github.com/android/compose-samples"
EDITOR = ["--activity", "org.fossify.filemanager.activities.ReadTextActivity", "--data", "file://{workdir}/list.txt"]
CASES = [
    Case("jetchat-same-message-twice", "Jetchat (Google sample) crashes when the same message is sent twice",
         SAMPLES, "0bc770fd", "Jetchat", run=jetchat_same_message_twice),
    Case("jetsnack-search-rotation", "Jetsnack (Google sample) loses the search query on rotation",
         SAMPLES, "0bc770fd", "Jetsnack", run=jetsnack_search_rotation),
    Case("calendar-644-before", "Fossify Calendar before #1041: each rotation stacks the month view again",
         "https://github.com/FossifyOrg/Calendar", "6b4528f24", task="testFossDebugUnitTest", run=calendar_stacks_on_rotation),
    Case("calendar-644-after", "Fossify Calendar with the fix: no stacking",
         "https://github.com/FossifyOrg/Calendar", "7165bae72", task="testFossDebugUnitTest", run=calendar_does_not_stack),
    Case("file-manager-111-back", "Fossify File Manager with #111 undone: the editor loses unsaved text on rotation",
         "https://github.com/FossifyOrg/File-Manager", "ef7524c0", task="testFossDebugUnitTest", start=EDITOR,
         patch=FILE_MANAGER_111, run=editor_text_lost_on_rotation),
    Case("file-manager-111-fixed", "Fossify File Manager as shipped: the edit survives",
         "https://github.com/FossifyOrg/File-Manager", "ef7524c0", task="testFossDebugUnitTest", start=EDITOR,
         run=editor_text_kept_on_rotation),
    Case("calculator-listener-leak", "A listener left on an app-wide object: leak reported with its chain",
         "https://github.com/FossifyOrg/Calculator", "48ea1b3", task="testFossDebugUnitTest", patch=LISTENER_LEAK,
         run=leak_with_chain),
    Case("calculator-no-leak", "Fossify Calculator as shipped: no app leak",
         "https://github.com/FossifyOrg/Calculator", "48ea1b3", task="testFossDebugUnitTest", run=no_app_leak),
]


# -------------------------------------------------------------------------------------------------------- running
def checkout(case: Case, workdir: Path) -> Path:
    src = workdir / "src" / f"{case.repo.rsplit('/', 1)[1]}-{case.commit}"
    if not (src / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", case.repo, str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "-f", case.commit], check=True)
    subprocess.run(["git", "-C", str(src), "clean", "-qfdx", "-e", ".gradle", "-e", "build"], check=True)
    for file, old, new in case.patch:
        path = src / case.project / file
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise AssertionError(f"patch target not found once in {file}")
        path.write_text(text.replace(old, new), encoding="utf-8")
    return src / case.project


def main() -> int:
    workdir = Path(sys.argv[1]).resolve()
    wanted = set(sys.argv[2:])
    (workdir / "logs").mkdir(parents=True, exist_ok=True)
    (workdir / "list.txt").write_text("Shopping list\nmilk\n")
    results = []
    for case in CASES:
        if wanted and case.name not in wanted:
            continue
        with open(workdir / "logs" / f"{case.name}.txt", "w") as log:
            try:
                project = checkout(case, workdir)
                s = Session(project, log)
                args = [a.replace("{workdir}", str(workdir)) for a in case.start]
                out = s("start", "--task", case.task, *args, timeout=3600)
                why = "the app did not start" if "app running" not in out else case.run(s)
            except Exception as e:  # a failed clone, patch or command is a FAIL with its reason
                why = f"{type(e).__name__}: {e}"
            finally:
                if "project" in locals():
                    Session(project, log)("stop")
        results.append((case, why))
        print(f"{'PASS' if not why else 'FAIL'}  {case.name}: {case.claim}" + (f"\n      {why}" if why else ""), flush=True)
    passed = sum(1 for _, why in results if not why)
    print(f"\n{passed} of {len(results)} claims reproduced")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
