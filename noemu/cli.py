#!/usr/bin/env python3
"""noemu: run your Android app and drive it, with no emulator.

The app runs on the JVM (Robolectric) inside a test the init script adds for this build only; nothing in your project
changes. Made for coding agents in sandboxes without an emulator, and for anyone who wants to see a screen in seconds.

    noemu start [--module :app] [--task testDebugUnitTest] [--activity com.x.MainActivity [--data URI]]
    noemu screen                 # numbered elements + a screenshot
    noemu tap 3 | noemu tap "Sign in"
    noemu type 2 "hello@example.com"
    noemu scroll down [N] | noemu back | noemu rotate [N] | noemu kill | noemu wait 2000
    noemu leaks                  # destroyed screens still in memory, and what holds them
    noemu shot out.png | noemu logs
    noemu crawl [STEPS] [MINUTES]  # tap and rotate through the app (default 200 steps, 10 min); report crashes and lost state
    noemu stop

NOEMU_GRADLE_ARGS adds arguments to the Gradle command (e.g. an init script for a mirror).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# In the project's .gradle folder: ignored by git in Android projects, and no build or clean deletes it.
STATE = Path(".gradle/noemu")


def send(line: str, timeout: float = 120) -> str:
    port_file = STATE / "port"
    if not port_file.exists():
        sys.exit("noemu: not running here; `noemu start` first")
    with socket.create_connection(("127.0.0.1", int(port_file.read_text())), timeout=timeout) as s:
        s.sendall((line + "\n").encode())
        data = b""
        while not data.endswith(b"\n.\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return data.decode(errors="replace").removesuffix("\n.\n").rstrip(".\n")


def start(args: list[str]) -> int:
    opts = {"--module": ":app", "--task": "testDebugUnitTest", "--activity": None, "--data": None}
    it = iter(args)
    for a in it:
        if a in opts:
            opts[a] = next(it)
    gradlew = Path("gradlew")
    if not gradlew.exists():
        sys.exit("noemu: run from the project root (where gradlew is)")
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "port").unlink(missing_ok=True)
    (STATE / "crash.txt").unlink(missing_ok=True)
    props = [f"-Dnoemu.module={opts['--module']}", f"-Dnoemu.driver={HERE}", f"-Dnoemu.out={STATE.resolve()}"]
    if opts["--activity"]:
        props.append(f"-Dnoemu.activity={opts['--activity']}")
    if opts["--data"]:
        props.append(f"-Dnoemu.data={opts['--data']}")
    extra = os.environ.get("NOEMU_GRADLE_ARGS", "").split()
    cmd = ["bash", "./gradlew", "--console=plain", "-I", str(HERE / "noemu.init.gradle"), *props, *extra,
           f"{opts['--module']}:{opts['--task']}", "--tests", "dev.noemu.NoemuDriver"]
    log = open(STATE / "gradle.log", "w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (STATE / "pid").write_text(str(proc.pid))
    print(f"noemu: building and starting the app (log: {STATE / 'gradle.log'})", flush=True)
    t0 = time.time()
    while not (STATE / "port").exists():
        if proc.poll() is not None:
            crash = STATE / "crash.txt"
            if crash.exists():
                sys.exit("noemu: the app crashed on launch:\n" + "\n".join(crash.read_text().splitlines()[:40]))
            log = STATE / "gradle.log"
            tail = log.read_text(errors="replace").splitlines()[-25:] if log.exists() else ["(the build deleted its log)"]
            sys.exit("noemu: the build stopped before the app started:\n" + "\n".join(tail))
        time.sleep(1)
    print(f"noemu: app running ({time.time() - t0:.0f}s)\n")
    print(send("screen"))
    return 0


def stop() -> int:
    try:
        print(send("quit", timeout=10))
    except (OSError, SystemExit):
        pid = STATE / "pid"
        if pid.exists():
            try:
                os.killpg(int(pid.read_text()), 15)
            except OSError:
                pass
    (STATE / "port").unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "start":
        return start(rest)
    if cmd == "stop":
        return stop()
    if cmd in ("screen", "tap", "type", "scroll", "back", "rotate", "kill", "leaks", "wait", "shot", "logs", "crawl"):
        line = " ".join([cmd] + rest)
        if cmd == "type" and len(rest) >= 2:
            line = f"type {rest[0]}\t{' '.join(rest[1:])}"
        if cmd == "shot" and rest:
            line = f"shot {Path(rest[0]).resolve()}"
        print(send(line, timeout=3600 if cmd == "crawl" else 900 if cmd == "leaks" else 120))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
