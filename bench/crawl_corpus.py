"""Crawl real open-source apps at their current version and keep the crash reports.

    python crawl_corpus.py OUT [STEPS]
Each app: shallow clone, `noemu start`, `noemu crawl STEPS`, `noemu stop`. Evidence and workspaces are retained.
"""
import json
import os
import sys
import time
from pathlib import Path

if __package__:
    from .commands import NOEMU, CommandError, app_environment, run_checked
else:
    from commands import NOEMU, CommandError, app_environment, run_checked
# (name, git url, gradle test task, extra gradle args)
APPS = [
    ("fossify-calculator", "https://github.com/FossifyOrg/Calculator", "testFossDebugUnitTest", ""),
    ("fossify-clock", "https://github.com/FossifyOrg/Clock", "testFossDebugUnitTest", ""),
    ("fossify-contacts", "https://github.com/FossifyOrg/Contacts", "testFossDebugUnitTest", ""),
    ("fossify-calendar", "https://github.com/FossifyOrg/Calendar", "testFossDebugUnitTest", ""),
    ("fossify-filemanager", "https://github.com/FossifyOrg/File-Manager", "testFossDebugUnitTest", ""),
    ("fossify-gallery", "https://github.com/FossifyOrg/Gallery", "testFossDebugUnitTest", ""),
    ("fossify-musicplayer", "https://github.com/FossifyOrg/Music-Player", "testFossDebugUnitTest", ""),
    ("fossify-voicerecorder", "https://github.com/FossifyOrg/Voice-Recorder", "testFossDebugUnitTest", ""),
    ("fossify-paint", "https://github.com/FossifyOrg/Paint", "testFossDebugUnitTest", ""),
    ("fossify-flashlight", "https://github.com/FossifyOrg/Flashlight", "testFossDebugUnitTest", ""),
    ("fossify-keyboard", "https://github.com/FossifyOrg/Keyboard", "testFossDebugUnitTest", ""),
    ("fossify-messages", "https://github.com/FossifyOrg/Messages", "testFossDebugUnitTest", ""),
]


def run(out: Path, steps: int) -> int:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    failed = not APPS
    for name, url, task, extra in APPS:
        report = out / f"{name}.txt"
        if report.exists():
            try:
                previous = json.loads(report.read_text().splitlines()[0])
                failed |= previous.get("version") != 2 or previous.get("status") != "passed"
            except (OSError, ValueError, IndexError, AttributeError):
                failed = True
            continue
        d = out / "src" / name
        result = {"version": 2, "app": name, "url": url, "commit": None,
                  "status": "inconclusive", "scope": "command_execution", "phase": "clone"}
        transcript = []
        attempted_start = False
        env = app_environment()
        env["NOEMU_GRADLE_ARGS"] = f"{os.environ.get('NOEMU_GRADLE_ARGS', '')} {extra}".strip()
        try:
            # Never erase a previous workspace: it may contain diagnostics or a
            # live session left by an interrupted run. Use a fresh output root.
            if d.exists():
                raise CommandError([], "inconclusive", f"workspace already exists: {d}; inspect it or use a fresh output root")
            d.parent.mkdir(parents=True, exist_ok=True)
            run_checked(["git", "clone", "-q", "--depth", "1", url, str(d)], cwd=out, timeout=600)
            result["phase"] = "revision"
            sha = run_checked(["git", "rev-parse", "HEAD"], cwd=d, timeout=30).stdout.strip()
            if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise CommandError([], "inconclusive", "checkout returned an invalid commit identity")
            result["commit"] = sha
            result["phase"] = "start"
            attempted_start = True
            t0 = time.monotonic()
            start = run_checked([*NOEMU, "start", "--task", task], cwd=d, env=env, timeout=3600)
            result["start_seconds"] = round(time.monotonic() - t0)
            transcript.append("start stdout:\n" + start.stdout + "\nstart stderr:\n" + start.stderr)
            result["phase"] = "crawl"
            crawl = run_checked([*NOEMU, "crawl", str(steps)], cwd=d, env=env, timeout=3600)
            transcript.append("crawl stdout:\n" + crawl.stdout + "\ncrawl stderr:\n" + crawl.stderr)
            result["summary"] = crawl.stdout.splitlines()[0] if crawl.stdout else ""
            result["status"] = "passed"
        except CommandError as error:
            result["status"] = error.status
            result["error"] = error.record
        finally:
            if attempted_start:
                try:
                    run_checked([*NOEMU, "stop"], cwd=d, env=env, timeout=120)
                except CommandError as error:
                    result["cleanup_error"] = error.record
                    if result["status"] == "passed":
                        result["status"] = "inconclusive"
            report.write_text(json.dumps(result) + "\n\n" + "\n\n".join(transcript))
        print(name, result["status"], result.get("summary") or result["phase"], flush=True)
        failed |= result["status"] != "passed"
    return int(failed)


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 150))
