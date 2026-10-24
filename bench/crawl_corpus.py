"""Crawl real open-source apps at their current version and keep the crash reports.

    python crawl_corpus.py OUT [STEPS]
Each app: shallow clone, `noemu start`, `noemu crawl STEPS`, `noemu stop`, build outputs deleted.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

NOEMU = Path(__file__).resolve().parents[1] / "noemu" / "cli.py"
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


def run(out: Path, steps: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name, url, task, extra in APPS:
        report = out / f"{name}.txt"
        if report.exists():
            continue
        d = out / "src" / name
        shutil.rmtree(d, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(d)], timeout=600)
        sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        wrapper = d / "gradle/wrapper/gradle-wrapper.properties"
        if wrapper.exists():  # reuse a Gradle already on disk
            text = wrapper.read_text()
            for v in ("9.8.1", "9.8", "9.7.0", "9.6.0", "9.5.0"):
                text = text.replace(f"gradle-{v}-bin", "gradle-9.7.1-bin")
            wrapper.write_text(text)
        env["NOEMU_GRADLE_ARGS"] = f"{os.environ.get('NOEMU_GRADLE_ARGS', '')} {extra}".strip()
        t0 = time.time()
        start = subprocess.run(["python3", str(NOEMU), "start", "--task", task], cwd=d, env=env, capture_output=True, text=True, timeout=3600)
        result = {"app": name, "url": url, "commit": sha, "start_seconds": round(time.time() - t0)}
        if "app running" not in start.stdout:
            result["error"] = (start.stdout + start.stderr)[-1500:]
            text = "start failed\n" + result["error"]
        else:
            crawl = subprocess.run(["python3", str(NOEMU), "crawl", str(steps)], cwd=d, env=env, capture_output=True, text=True, timeout=3600)
            text = crawl.stdout + crawl.stderr
            result["summary"] = text.splitlines()[0] if text else ""
        subprocess.run(["python3", str(NOEMU), "stop"], cwd=d, env=env, capture_output=True, timeout=120)
        report.write_text(json.dumps(result) + "\n\n" + text)
        print(name, result.get("summary") or "start failed", flush=True)
        for b in [d / "build", d / "app/build", d / ".gradle"]:
            shutil.rmtree(b, ignore_errors=True)


if __name__ == "__main__":
    run(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 150)
