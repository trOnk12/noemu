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

Many instances for parallel agents (fleet): build once, then start each app JVM without Gradle.

    noemu build [--module :app] [--task testDebugUnitTest]   # compile and record how the app starts
    noemu pool N                 # start N instances and put them to sleep (each pays the full start, ~25 s)
    noemu spawn                  # wake a pooled instance (well under a second), or start one if the pool is
                                 # empty; prints "ID PORT"; the pool refills in the background
    noemu --id ID screen         # any command above, on that instance (or NOEMU_ID=ID)
    noemu sleep ID | noemu wake ID   # freeze / resume its JVM: no CPU while asleep, state kept
    noemu destroy ID|all         # end it at once
    noemu list                   # instances: id, state, port, memory
    noemu mcp                    # the same as an MCP server (stdio) for coding agents
    noemu doctor [--module :app] [--task ...]   # what will stand in the way (reads the project, runs nothing)

NOEMU_GRADLE_ARGS adds arguments to the Gradle command (e.g. an init script for a mirror). NOEMU_SETTLE_MS (default
4000) caps how long an action waits for the screen to settle (changing numbers, like a clock, don't count).
NOEMU_DEPS adds test-runtime dependencies (comma-separated), e.g. JVM builds of native libraries the app loads.
NOEMU_SHOTS=0 skips the screenshot after every action (about 0.3 s each; `shot` still makes one). NOEMU_XMX
(default 1g) is each fleet instance's heap; NOEMU_JVM_ARGS (default
"-XX:+UseSerialGC -XX:TieredStopAtLevel=1": smaller, faster to start) sets each instance's JVM options.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# In the project's .gradle folder: ignored by git in Android projects, and no build or clean deletes it.
STATE = Path(".gradle/noemu")
FLEET = Path(".gradle/noemu-fleet")
INSTANCE: str | None = None  # fleet instance the session commands go to (--id or NOEMU_ID); None: `noemu start`'s


def target() -> Path:
    return FLEET / INSTANCE if INSTANCE else STATE


def send(line: str, timeout: float = 120) -> str:
    port_file = target() / "port"
    if not port_file.exists():
        sys.exit(f"noemu: instance {INSTANCE} isn't running" if INSTANCE else "noemu: not running here; `noemu start` first")
    with socket.create_connection(("127.0.0.1", int(port_file.read_text())), timeout=timeout) as s:
        s.sendall((line + "\n").encode())
        data = b""
        while not data.endswith(b"\n.\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return data.decode(errors="replace").removesuffix("\n.\n").rstrip(".\n")


def gradle(opts: dict, props: list[str], log: Path) -> subprocess.Popen:
    if not Path("gradlew").exists():
        sys.exit("noemu: run from the project root (where gradlew is)")
    props = [f"-Dnoemu.module={opts['--module']}", f"-Dnoemu.driver={HERE}", f"-Dnoemu.out={STATE.resolve()}", *props]
    props += [f"-Dnoemu.{k}={os.environ[v]}" for k, v in (("settle", "NOEMU_SETTLE_MS"), ("shots", "NOEMU_SHOTS"),
                                                          ("deps", "NOEMU_DEPS")) if os.environ.get(v)]
    extra = os.environ.get("NOEMU_GRADLE_ARGS", "").split()
    cmd = ["bash", "./gradlew", "--console=plain", "-I", str(HERE / "noemu.init.gradle"), *props, *extra,
           f"{opts['--module']}:{opts['--task']}", "--tests", "dev.noemu.NoemuDriver"]
    return subprocess.Popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True)


def options(args: list[str]) -> dict:
    opts = {"--module": ":app", "--task": "testDebugUnitTest", "--activity": None, "--data": None}
    it = iter(args)
    for a in it:
        if a in opts:
            opts[a] = next(it)
    return opts


def build_failed(log: Path) -> None:
    tail = log.read_text(errors="replace").splitlines()[-25:] if log.exists() else ["(the build deleted its log)"]
    sys.exit("noemu: the build stopped before the app started:\n" + "\n".join(tail))


def start(args: list[str]) -> int:
    opts = options(args)
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "port").unlink(missing_ok=True)
    (STATE / "crash.txt").unlink(missing_ok=True)
    props = []
    if opts["--activity"]:
        props.append(f"-Dnoemu.activity={opts['--activity']}")
    if opts["--data"]:
        props.append(f"-Dnoemu.data={opts['--data']}")
    proc = gradle(opts, props, STATE / "gradle.log")
    (STATE / "pid").write_text(str(proc.pid))
    print(f"noemu: building and starting the app (log: {STATE / 'gradle.log'})", flush=True)
    t0 = time.time()
    while not (STATE / "port").exists():
        if proc.poll() is not None:
            crash = STATE / "crash.txt"
            if crash.exists():
                sys.exit("noemu: the app crashed on launch:\n" + "\n".join(crash.read_text().splitlines()[:40]))
            build_failed(STATE / "gradle.log")
        time.sleep(1)
    print(f"noemu: app running ({time.time() - t0:.0f}s)\n")
    print(send("screen"))
    return 0


def stop() -> int:
    try:
        print(send("quit", timeout=10))
    except (OSError, SystemExit):
        pid = target() / "pid"
        if pid.exists():
            try:
                os.killpg(int(pid.read_text()), signal.SIGTERM)
            except OSError:
                pass
    (target() / "port").unlink(missing_ok=True)
    return 0


# Fleet ------------------------------------------------------------------------------------------------------------

def build(args: list[str]) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    spec = STATE / "launch.json"
    spec.unlink(missing_ok=True)
    print("noemu: building (log: .gradle/noemu/build.log)", flush=True)
    t0 = time.time()
    if gradle(options(args), ["-Dnoemu.recordOnly=true"], STATE / "build.log").wait() != 0 or not spec.exists():
        build_failed(STATE / "build.log")
    stale = [d for d in instances() if d.joinpath("build").read_text() != build_id()]
    for d in stale:  # a pooled instance runs the old code: never hand it out
        if state_of(d) == "pooled":
            destroy_one(d)
    print(f"noemu: built ({time.time() - t0:.0f}s); fleet instances start from this build")
    return 0


def build_id() -> str:
    spec = STATE / "launch.json"
    if not spec.exists():
        sys.exit("noemu: no build recorded; `noemu build` (or `noemu start`) first")
    return str(spec.stat().st_mtime_ns)


def launch() -> Path:
    """Starts one instance from the recorded build; returns its directory (port appears when it is up)."""
    spec = json.loads((STATE / "launch.json").read_text())
    FLEET.mkdir(parents=True, exist_ok=True)
    while True:  # several agents may launch at once: the first to create a directory owns that id
        taken = [int(d.name) for d in FLEET.iterdir() if d.name.isdigit()]
        out = FLEET / str(max(taken + [-1]) + 1)
        try:
            out.mkdir()
            break
        except FileExistsError:
            continue
    args = [a for a in spec["jvmArgs"] if not a.startswith(("-Dnoemu.out=", "-Dnoemu.settle=", "-Dnoemu.shots=", "-Xmx"))]
    (out / "tmp").mkdir()  # Robolectric unpacks its native runtime (about 200 MB) and the app's data here
    args += [f"-Xmx{os.environ.get('NOEMU_XMX', '1g')}", f"-Dnoemu.out={out.resolve()}",
             f"-Djava.io.tmpdir={(out / 'tmp').resolve()}"]
    args += [f"-Dnoemu.{k}={os.environ[v]}" for k, v in (("settle", "NOEMU_SETTLE_MS"), ("shots", "NOEMU_SHOTS"))
             if os.environ.get(v)]
    # Many small app JVMs: the serial collector and the fast JIT tier only start about 1 s sooner and use about 17%
    # less memory (388 against 468 MB on the KMP demo) with taps as fast. NOEMU_JVM_ARGS replaces them ("" for none).
    args += os.environ.get("NOEMU_JVM_ARGS", "-XX:+UseSerialGC -XX:TieredStopAtLevel=1").split()
    (out / "build").write_text(build_id())
    proc = subprocess.Popen([spec["java"], *args, "-cp", spec["classpath"], "org.junit.runner.JUnitCore",
                             "dev.noemu.NoemuDriver"], cwd=spec["workingDir"], stdout=open(out / "log.txt", "w"),
                            stderr=subprocess.STDOUT, start_new_session=True)
    (out / "pid").write_text(str(proc.pid))
    (out / "state").write_text("starting")
    return out


def wait_up(dirs: list[Path], timeout: float = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout and any(not (d / "port").exists() and alive(d) for d in dirs):
        time.sleep(0.2)


def alive(d: Path) -> bool:
    try:
        os.kill(int((d / "pid").read_text()), 0)
        return (d / "state").read_text() != "destroyed"
    except (OSError, ValueError):
        return False


def state_of(d: Path) -> str:
    return (d / "state").read_text() if (d / "state").exists() else "running"


def instances() -> list[Path]:
    dirs = [d for d in FLEET.glob("*") if d.name.isdigit() and (d / "pid").exists() and alive(d)] if FLEET.exists() else []
    return sorted(dirs, key=lambda d: int(d.name))


def signal_to(d: Path, sig: int, state: str) -> None:
    os.killpg(int((d / "pid").read_text()), sig)
    (d / "state").write_text(state)


def robolectric_dirs(d: Path) -> set[Path]:
    """Robolectric's temporary directories this instance uses: the app's data (files, databases, preferences) and
    its unpacked native runtime (about 200 MB). A killed JVM leaves both behind."""
    found = set()
    data = Path((d / "datadir").read_text()) if (d / "datadir").exists() else None
    found |= {p for p in ([data] + list(data.parents) if data else []) if p.name.startswith("robolectric-")}
    try:  # what the JVM has mapped (Linux): the native runtime's libraries
        maps = Path(f"/proc/{(d / 'pid').read_text()}/maps").read_text()
        found |= {Path(m) for m in set(re.findall(r"(/\S*?/robolectric-[^/\s]+)", maps))}
    except (OSError, ValueError):
        pass
    return found


def destroy_one(d: Path) -> None:
    dirs = robolectric_dirs(d)
    try:
        signal_to(d, signal.SIGKILL, "destroyed")
    except OSError:
        (d / "state").write_text("destroyed")
    for root in dirs | {d / "tmp"}:
        shutil.rmtree(root, ignore_errors=True)


def memory(d: Path) -> int:
    try:
        return int(Path(f"/proc/{(d / 'pid').read_text()}/status").read_text().split("VmRSS:")[1].split()[0]) // 1024
    except (OSError, IndexError, ValueError):
        return 0


def pool(n: int) -> int:
    build_id()
    t0 = time.time()
    dirs = [launch() for _ in range(n)]
    wait_up(dirs)
    for d in dirs:
        if (d / "port").exists():
            signal_to(d, signal.SIGSTOP, "pooled")
            print(f"{d.name}: in the pool after {time.time() - t0:.0f}s, {memory(d)} MB")
        else:
            destroy_one(d)
            print(f"{d.name}: FAILED to start; see {d / 'log.txt'}")
    return 0


def spawn(refill: bool = True) -> int:
    current = build_id()
    pooled = [d for d in instances() if state_of(d) == "pooled" and (d / "build").read_text() == current]
    d = next((d for d in pooled if claim(d)), None)
    if d:
        signal_to(d, signal.SIGCONT, "running")
    else:
        d = launch()
        wait_up([d])
        if not (d / "port").exists():
            destroy_one(d)
            sys.exit(f"noemu: the instance didn't start; see {d / 'log.txt'}")
        (d / "state").write_text("running")
    if refill and d.joinpath("claimed").exists():  # keep the pool at its size: start a replacement, detached, that sleeps once up
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_refill"], cwd=os.getcwd(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(f"{d.name} {(d / 'port').read_text()}")
    return 0


def claim(d: Path) -> bool:
    """Takes a pooled instance for this caller only: agents spawning at once never get the same one."""
    try:
        os.close(os.open(d / "claimed", os.O_CREAT | os.O_EXCL))
        return True
    except FileExistsError:
        return False


def fleet(cmd: str, rest: list[str]) -> int:
    if cmd == "build":
        return build(rest)
    if cmd == "pool":
        return pool(int(rest[0]) if rest else 2)
    if cmd == "spawn":
        return spawn()
    if cmd == "_refill":
        return pool(1)
    if cmd == "list":
        for d in instances():
            port = (d / "port").read_text() if (d / "port").exists() else "-"
            print(f"{d.name}\t{state_of(d)}\tport {port}\t{memory(d)} MB")
        return 0
    if not rest:
        sys.exit(f"noemu: {cmd} needs an instance id (noemu list)")
    if rest[0] == "all" and cmd == "destroy" and FLEET.exists():  # also what instances that died left behind
        for d in FLEET.iterdir():
            if d.name.isdigit() and not alive(d):
                shutil.rmtree(d / "tmp", ignore_errors=True)
    dirs = instances() if rest[0] == "all" else [FLEET / rest[0]]
    for d in dirs:
        if not (d / "pid").exists():
            sys.exit(f"noemu: no instance {d.name}")
        if cmd == "destroy":
            destroy_one(d)
        else:
            signal_to(d, signal.SIGSTOP if cmd == "sleep" else signal.SIGCONT, "asleep" if cmd == "sleep" else "running")
        print(f"{d.name} {'destroyed' if cmd == 'destroy' else 'asleep' if cmd == 'sleep' else 'awake'}")
    return 0


# Doctor ----------------------------------------------------------------------------------------------------------

# Android-only native libraries, and what to do about each on the JVM. Matched against the build files' text.
NATIVE = [
    (r"core[-.]?crypto", "Wire core-crypto: add its JVM build, NOEMU_DEPS=com.wire:core-crypto-jvm:<core-crypto version>"),
    (r"net\.zetetic|sqlcipher", "SQLCipher: Android-only; fine if the app doesn't encrypt its database in debug builds "
                               "(Wire: off for debug on API 30+)"),
    (r"com\.wire:avs|\bavs\b", "Wire AVS (calls): Android-only, calls won't work"),
    (r"\bjna\b", "JNA: loads host libraries; check the artifact ships Linux/macOS ones"),
    (r"firebase|play[-.]services|\bgms\b", "Google Play services / Firebase: what needs Play services won't work "
                                          "(sign-in, push, maps tiles); the app itself usually starts"),
    (r"tensorflow|onnx|media[-.]?pipe", "an on-device ML runtime: Android-only native code"),
]


def doctor(args: list[str]) -> int:
    """Reads the project (nothing runs) and says what will stand in the way of `noemu build`."""
    import shutil as sh
    opts = options(args)
    ok, warn, bad = [], [], []
    root = Path(".")
    (ok if (root / "gradlew").exists() else bad).append("gradlew in this directory" if (root / "gradlew").exists()
                                                        else "no gradlew here: run from the project root")
    java_home = os.environ.get("JAVA_HOME")
    jdks = [p.name for p in Path("/usr/lib/jvm").glob("*")] if Path("/usr/lib/jvm").exists() else []
    jdks += [p.name for p in Path("/Library/Java/JavaVirtualMachines").glob("*")] \
        if Path("/Library/Java/JavaVirtualMachines").exists() else []
    (ok if any("21" in j for j in jdks) or (java_home and "21" in java_home) else warn).append(
        "a JDK 21 is installed" if any("21" in j for j in jdks) or (java_home and "21" in java_home) else
        "no JDK 21 found: Robolectric runs SDK 36 only on Java 21+ (Gradle looks for one)")
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    props = (root / "local.properties").read_text(errors="replace") if (root / "local.properties").exists() else ""
    (ok if sdk or "sdk.dir" in props else bad).append(
        "Android SDK set" if sdk or "sdk.dir" in props else "no Android SDK: set ANDROID_HOME or sdk.dir")
    module = Path(opts["--module"].strip(":").replace(":", "/"))
    build_file = next((module / f for f in ("build.gradle.kts", "build.gradle") if (module / f).exists()), None)
    text = build_file.read_text(errors="replace") if build_file else ""
    if not build_file:
        bad.append(f"no build file in {module}/: pass --module (the module that applies com.android.application)")
    elif not re.search(r"com\.android\.application|androidApplication|android\.application|AndroidApplication", text):
        warn.append(f"{build_file} doesn't visibly apply com.android.application (a convention plugin may): "
                    "noemu needs the app module")
    else:
        ok.append(f"app module {opts['--module']}")
    if re.search(r"productFlavors|Flavors", text) and opts["--task"] == "testDebugUnitTest":
        warn.append("the app has product flavors: pass --task test<Flavor>DebugUnitTest")
    if "useJUnitPlatform" in text or "junit5" in text.lower():
        ok.append("JUnit 5 in the module: noemu adds the vintage engine for its JUnit 4 driver")
    # The module's own build file, and the version catalogs' entries (an entry may be unused: said so).
    catalogs = "\n".join(p.read_text(errors="replace") for p in list(root.glob("**/libs.versions.toml"))[:4]
                         if "build" not in p.parts)
    for pattern, advice in NATIVE:
        if re.search(pattern, text, re.I):
            warn.append(advice)
        elif re.search(pattern, catalogs, re.I):
            warn.append(advice + " (listed in a version catalog; ignore if the app doesn't use it)")
    free = sh.disk_usage(".").free // 2**30
    (ok if free >= 4 else warn).append(f"{free} GB free" + ("" if free >= 4 else
                                       ": each instance unpacks about 200 MB, a build needs several GB"))
    for mark, items in (("ok  ", ok), ("warn", warn), ("FAIL", bad)):
        for item in items:
            print(f"{mark} {item}")
    return 1 if bad else 0


# MCP --------------------------------------------------------------------------------------------------------------

ID = {"id": {"type": "string", "description": "instance id, from spawn"}}
TOOLS = {
    "spawn": ("Get a running app instance: wakes a pooled one (under a second) or starts one. Returns its id and "
              "first screen. Call build first after changing code.", {}, []),
    "screen": ("The instance's screen: numbered elements with labels, states and what can be done with them.", ID, ["id"]),
    "tap": ("Tap an element by its number or its label; returns the new screen.",
            {**ID, "target": {"type": "string"}}, ["id", "target"]),
    "type": ("Type text into a field (by number or label); returns the new screen.",
             {**ID, "target": {"type": "string"}, "text": {"type": "string"}}, ["id", "target", "text"]),
    "scroll": ("Scroll (down by default; up, left, right).", {**ID, "direction": {"type": "string"}}, ["id"]),
    "back": ("Press back.", ID, ["id"]),
    "rotate": ("Rotate the device; shows what the rotation lost.", ID, ["id"]),
    "wait": ("Let the app run for some milliseconds, then return the screen.",
             {**ID, "ms": {"type": "integer"}}, ["id"]),
    "shot": ("A screenshot of the instance, as an image.", ID, ["id"]),
    "logs": ("The app's recent log lines.", ID, ["id"]),
    "sleep": ("Freeze the instance (no CPU, state kept).", ID, ["id"]),
    "wake": ("Resume a frozen instance.", ID, ["id"]),
    "destroy": ("End the instance and remove its files.", ID, ["id"]),
    "list": ("All instances: id, state, port, memory.", {}, []),
    "pool": ("Start N instances asleep, so later spawns are instant.", {"n": {"type": "integer"}}, ["n"]),
    "build": ("Compile the app and record how it starts (after every code change; pooled instances of the old code "
              "are dropped).", {"module": {"type": "string"}, "task": {"type": "string"}}, []),
}


def run_tool(name: str, a: dict) -> list:
    """Runs one tool through the same functions as the command line; returns MCP content."""
    import contextlib, io, base64
    global INSTANCE
    out = io.StringIO()
    INSTANCE = str(a["id"]) if "id" in a else None
    try:
        with contextlib.redirect_stdout(out):
            if name == "spawn":
                spawn()
                INSTANCE = out.getvalue().split()[0]
                print("\n" + send("screen"))
            elif name == "build":
                build([x for k in ("module", "task") if a.get(k) for x in (f"--{k}", a[k])])
            elif name == "pool":
                pool(int(a["n"]))
            elif name in ("list", "sleep", "wake", "destroy"):
                fleet(name, [str(a["id"])] if "id" in a else [])
            elif name == "shot":
                path = (target() / "mcp-shot.png").resolve()
                send(f"shot {path}")
                return [{"type": "image", "data": base64.b64encode(path.read_bytes()).decode(), "mimeType": "image/png"}]
            else:
                line = {"tap": f"tap {a.get('target', '')}", "type": f"type {a.get('target', '')}\t{a.get('text', '')}",
                        "scroll": f"scroll {a.get('direction', 'down')}", "wait": f"wait {a.get('ms', 1000)}"}.get(name, name)
                print(send(line))
    except SystemExit as e:
        raise RuntimeError(str(e.code) if e.code not in (None, 0) else out.getvalue()) from None
    return [{"type": "text", "text": out.getvalue().strip()}]


def mcp() -> int:
    """A stdio MCP server (JSON-RPC 2.0, one message per line) exposing the fleet and session commands as tools."""
    for raw in sys.stdin:
        if not raw.strip():
            continue
        msg = json.loads(raw)
        if "id" not in msg:  # a notification (notifications/initialized): no answer
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "noemu", "version": "0.2"}}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": {
                    "type": "object", "properties": props, "required": req}} for n, (d, props, req) in TOOLS.items()]}
            elif method == "tools/call":
                try:
                    result = {"content": run_tool(params["name"], params.get("arguments") or {})}
                except (RuntimeError, OSError, KeyError, ValueError) as e:
                    text = str(e) if str(e).startswith("noemu:") else f"noemu: {e}"
                    result = {"content": [{"type": "text", "text": text}], "isError": True}
            elif method == "ping":
                result = {}
            else:
                raise LookupError(method)
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"unknown method {e}"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    global INSTANCE
    argv = sys.argv[1:] if argv is None else argv
    INSTANCE = os.environ.get("NOEMU_ID") or None
    if len(argv) >= 2 and argv[0] == "--id":
        INSTANCE, argv = argv[1], argv[2:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "mcp":
        return mcp()
    if cmd == "doctor":
        return doctor(rest)
    if cmd == "start":
        return start(rest)
    if cmd == "stop":
        return stop()
    if cmd in ("build", "pool", "spawn", "_refill", "list", "sleep", "wake", "destroy"):
        return fleet(cmd, rest)
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
