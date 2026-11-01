#!/usr/bin/env python3
"""noemu: run your Android app and drive it, with no emulator.

The app runs on the JVM (Robolectric) inside a test the init script adds for this build only; nothing in your project
changes. Made for coding agents in sandboxes without an emulator, and for anyone who wants to see a screen in seconds.

    noemu start [--module :app] [--task testDebugUnitTest] [--activity com.x.MainActivity [--data URI]]
    noemu screen                 # numbered elements + a screenshot
    noemu tap 3 | noemu tap "Sign in"
    noemu type 2 "hello@example.com"
    noemu scroll down [N] | noemu back | noemu rotate [N] | noemu restore-activity | noemu wait 2000
    noemu kill                  # legacy alias for restore-activity; application statics survive
    noemu open .SettingsActivity | noemu open myapp://chat/42   # an activity, or a deep link
    noemu network off | noemu network on   # airplane mode: open connections closed, new ones refused, app told
    noemu network slow 300       # 300 ms more each way on every connection (0: none)
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
NOEMU_SHOTS=0 skips the screenshot after every action (about 0.3 s each; `shot` still makes one). NOEMU_IDLE_MIN ends a fleet instance after that many minutes without a
command (asleep in the pool doesn't count). NOEMU_XMX (default 1g) is each fleet instance's heap; NOEMU_JVM_ARGS (default
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
import uuid
from pathlib import Path

from noemu import processes
from noemu.protocol import Command, Result, NoemuError, MAX_MESSAGE_BYTES

HERE = Path(__file__).resolve().parent
# In the project's .gradle folder: ignored by git in Android projects, and no build or clean deletes it.
STATE = Path(".gradle/noemu")
FLEET = Path(".gradle/noemu-fleet")
INSTANCE: str | None = None  # fleet instance the session commands go to (--id or NOEMU_ID); None: `noemu start`'s


def instance_path(name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"0|[1-9][0-9]{0,9}", name):
        sys.exit("noemu: invalid instance id; expected a nonnegative decimal number")
    path = FLEET / name
    if path.is_symlink() or path.resolve().parent != FLEET.resolve():
        sys.exit("noemu: instance directory escapes the fleet")
    return path


def target() -> Path:
    return instance_path(INSTANCE) if INSTANCE is not None else STATE


def request(command: Command, timeout: float = 120) -> Result:
    request_id = str(uuid.uuid4())
    payload = command.encode(request_id, timeout)
    port_file = target() / "port"
    if not port_file.exists():
        sys.exit(f"noemu: instance {INSTANCE} isn't running" if INSTANCE else "noemu: not running here; `noemu start` first")
    state = state_of(target()) if INSTANCE else "running"
    if state == "pooled":
        sys.exit(f"noemu: instance {INSTANCE} is asleep in the pool; `noemu spawn` hands out a pooled instance")
    if state == "asleep":
        sys.exit(f"noemu: instance {INSTANCE} is asleep; `noemu wake {INSTANCE}` first")
    deadline = time.monotonic() + timeout
    try:
        port = int(port_file.read_text())
        if not 1 <= port <= 65535:
            raise NoemuError("invalid_state", "invalid driver port")
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            connection.settimeout(remaining)
            connection.sendall(payload)
            data = bytearray()
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                chunk = connection.recv(min(65536, MAX_MESSAGE_BYTES + 1 - len(data)))
                if not chunk:
                    raise NoemuError("protocol_error", "driver closed the connection before a complete response")
                data.extend(chunk)
                if len(data) > MAX_MESSAGE_BYTES:
                    raise NoemuError("message_too_large", "driver response exceeds 1 MiB")
            return Result.decode(bytes(data), request_id, INSTANCE)
    except TimeoutError:
        raise NoemuError("deadline_exceeded", f"no answer in {timeout:g} s; outcome unknown; do not retry a mutation blindly") from None


def send(command: Command | str, timeout: float = 120) -> str:
    return request(Command.legacy(command) if isinstance(command, str) else command, timeout).text()


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
        if a not in opts:
            sys.exit(f"noemu: unknown build option {a}")
        value = next(it, None)
        if value is None or value.startswith("--"):
            sys.exit(f"noemu: {a} requires a value")
        opts[a] = value
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
    processes.remember(STATE, proc.pid)
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
    # A Gradle wrapper PID is not an app process identity. Never kill an
    # unverified process group as a fallback for an unreachable driver.
    print(send("quit", timeout=10))
    (target() / "port").unlink(missing_ok=True)
    return 0


# Fleet ------------------------------------------------------------------------------------------------------------

def build(args: list[str]) -> int:
    opts = options(args)
    STATE.mkdir(parents=True, exist_ok=True)
    spec = STATE / "launch.json"
    spec.unlink(missing_ok=True)
    print("noemu: building (log: .gradle/noemu/build.log)", flush=True)
    t0 = time.time()
    if gradle(opts, ["-Dnoemu.recordOnly=true"], STATE / "build.log").wait() != 0 or not spec.exists():
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
    args = [a for a in spec["jvmArgs"] if not a.startswith(("-Dnoemu.out=", "-Dnoemu.settle=", "-Dnoemu.shots=", "-Dnoemu.instance=", "-Xmx"))]
    (out / "tmp").mkdir()  # Robolectric unpacks its native runtime (about 200 MB) and the app's data here
    args += [f"-Xmx{os.environ.get('NOEMU_XMX', '1g')}", f"-Dnoemu.out={out.resolve()}", f"-Dnoemu.instance={out.name}",
             f"-Djava.io.tmpdir={(out / 'tmp').resolve()}"]
    args += [f"-Dnoemu.{k}={os.environ[v]}" for k, v in (("settle", "NOEMU_SETTLE_MS"), ("shots", "NOEMU_SHOTS"),
                                                         ("idle", "NOEMU_IDLE_MIN")) if os.environ.get(v)]
    # Many small app JVMs: the serial collector and the fast JIT tier only start about 1 s sooner and use about 17%
    # less memory (388 against 468 MB on the KMP demo) with taps as fast. NOEMU_JVM_ARGS replaces them ("" for none).
    args += os.environ.get("NOEMU_JVM_ARGS", "-XX:+UseSerialGC -XX:TieredStopAtLevel=1").split()
    (out / "build").write_text(build_id())
    proc = subprocess.Popen([spec["java"], *args, "-cp", spec["classpath"], "org.junit.runner.JUnitCore",
                             "dev.noemu.NoemuDriver"], cwd=spec["workingDir"], stdout=open(out / "log.txt", "w"),
                            stderr=subprocess.STDOUT, start_new_session=True)
    processes.remember(out, proc.pid)
    (out / "state").write_text("starting")
    return out


def wait_up(dirs: list[Path], timeout: float = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout and any(not (d / "port").exists() and alive(d) for d in dirs):
        time.sleep(0.2)


def alive(d: Path) -> bool:
    return not d.is_symlink() and state_of(d) != "destroyed" and processes.alive(d)


def state_of(d: Path) -> str:
    return (d / "state").read_text() if (d / "state").exists() else "running"


def instances() -> list[Path]:
    dirs = [d for d in FLEET.glob("*") if re.fullmatch(r"0|[1-9][0-9]{0,9}", d.name) and not d.is_symlink() and (d / "pid").exists() and alive(d)] if FLEET.exists() else []
    return sorted(dirs, key=lambda d: int(d.name))


def signal_to(d: Path, sig: int, state: str) -> None:
    processes.send_signal(d, sig)
    (d / "state").write_text(state)


def destroy_one(d: Path) -> None:
    processes.terminate(d)
    (d / "state").write_text("destroyed")
    (d / "port").unlink(missing_ok=True)
    # Only this instance's allocated root is owned by NoEmu. datadir and
    # /proc mappings are observations, not permission to delete other paths.
    temporary = d / "tmp"
    if temporary.is_symlink():
        temporary.unlink()
    elif temporary.exists():
        shutil.rmtree(temporary)


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
    failed = False
    for d in dirs:
        if (d / "port").exists():
            signal_to(d, signal.SIGSTOP, "pooled")
            print(f"{d.name}: in the pool after {time.time() - t0:.0f}s, {memory(d)} MB")
        else:
            failed = True
            if alive(d):
                destroy_one(d)
            print(f"{d.name}: FAILED to start; see {d / 'log.txt'}")
    return 1 if failed else 0


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
        subprocess.Popen([sys.executable, "-m", "noemu", "_refill"], cwd=os.getcwd(),
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
        if len(rest) > 1:
            raise NoemuError("invalid_argument", "pool accepts one size")
        return pool(Command.integer(rest[0], 1, 4) if rest else 2)
    if cmd == "spawn":
        if rest not in ([], ["--no-refill"]):
            raise NoemuError("invalid_argument", "spawn accepts only --no-refill")
        return spawn(refill=not rest)
    if cmd == "_refill":
        return pool(1)
    if cmd == "list":
        if rest:
            raise NoemuError("invalid_argument", "list takes no arguments")
        for d in instances():
            port = (d / "port").read_text() if (d / "port").exists() else "-"
            print(f"{d.name}\t{state_of(d)}\tport {port}\t{memory(d)} MB")
        return 0
    if not rest:
        sys.exit(f"noemu: {cmd} needs an instance id (noemu list)")
    if len(rest) != 1:
        sys.exit(f"noemu: {cmd} requires exactly one instance id")
    dirs = instances() if rest[0] == "all" else [instance_path(rest[0])]
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

ID = {"id": {"type": "string", "pattern": "^(0|[1-9][0-9]{0,9})$", "description": "instance id, from spawn"}}
TOOLS = {
    "spawn": ("Get a running app instance: wakes a pooled one (under a second) or starts one. Returns its id and "
              "first screen. Call build first after changing code.", {}, []),
    "screen": ("The instance's screen: numbered elements with labels, states and what can be done with them.", ID, ["id"]),
    "tap": ("Tap an element by its number or its label; returns the new screen.",
            {**ID, "target": {"type": "string"}}, ["id", "target"]),
    "type": ("Type text into a field (by number or label); returns the new screen.",
             {**ID, "target": {"type": "string"}, "text": {"type": "string"}}, ["id", "target", "text"]),
    "scroll": ("Scroll a semantic container. Supply target when more than one container matches the axis.",
               {**ID, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "target": {"type": "string", "minLength": 1}}, ["id"]),
    "back": ("Press back.", ID, ["id"]),
    "rotate": ("Rotate the activity and observe its state.",
               {**ID, "count": {"type": "integer", "minimum": 1, "maximum": 100}}, ["id"]),
    "restore-activity": ("Restore activity saved state in the same JVM; application statics and singletons survive.", ID, ["id"]),
    "leaks": ("Inspect retained destroyed activities. This may require heap analysis.", ID, ["id"]),
    "crawl": ("Explore within action and time budgets; report crashes, state loss and unresolved limitations.",
              {**ID, "steps": {"type": "integer", "minimum": 1, "maximum": 10000},
               "minutes": {"type": "integer", "minimum": 1, "maximum": 60}}, ["id"]),
    "wait": ("Let the app run for some milliseconds, then return the screen.",
             {**ID, "ms": {"type": "integer", "minimum": 0, "maximum": 120000}}, ["id"]),
    "open": ("Open an activity (.SettingsActivity or a full class name) or a deep link (myapp://...) in the instance; "
             "returns the new screen.", {**ID, "target": {"type": "string"}}, ["id", "target"]),
    "network": ("Airplane mode for the instance: 'off' closes its open connections (a websocket too), refuses new ones "
                "and tells the app; 'on' brings it back (returns the new screen). 'slow' with delay_ms adds that "
                "latency each way.",
                {**ID, "state": {"type": "string", "enum": ["on", "off", "slow"]}, "delay_ms": {"type": "integer", "minimum": 0, "maximum": 60000}},
                ["id", "state"]),
    "shot": ("A screenshot of the instance, as an image.", ID, ["id"]),
    "logs": ("The app's recent log lines.", ID, ["id"]),
    "sleep": ("Freeze the instance (no CPU, state kept).", ID, ["id"]),
    "wake": ("Resume a frozen instance.", ID, ["id"]),
    "destroy": ("End the instance and remove its files.", ID, ["id"]),
    "list": ("All instances: id, state, port, memory.", {}, []),
    "pool": ("Start N instances asleep, so later spawns are instant.", {"n": {"type": "integer", "minimum": 1, "maximum": 4}}, ["n"]),
    "build": ("Compile the app and record how it starts (after every code change; pooled instances of the old code "
              "are dropped).", {"module": {"type": "string"}, "task": {"type": "string"}}, []),
}


def run_tool(name: str, a: dict):
    """Runs one tool through the same functions as the command line; returns MCP content."""
    import contextlib, io, base64
    from noemu.protocol import ToolOutput
    global INSTANCE
    out = io.StringIO()
    result = None
    def execute(command: Command) -> str:
        nonlocal result
        result = request(command, timeout=command.timeout_seconds)
        return result.text()
    if name not in TOOLS:
        raise NoemuError("unsupported", f"unknown tool {name}")
    previous = INSTANCE
    INSTANCE = a.get("id")
    try:
        if INSTANCE is not None:
            instance_path(INSTANCE)
        with contextlib.redirect_stdout(out):
            if name == "spawn":
                spawn()
                INSTANCE = out.getvalue().split()[0]
                print("\n" + execute(Command("screen")))
            elif name == "build":
                build([x for k in ("module", "task") if a.get(k) for x in (f"--{k}", a[k])])
            elif name == "pool":
                if pool(int(a["n"])):
                    raise NoemuError("startup_failed", out.getvalue())
            elif name in ("list", "sleep", "wake", "destroy"):
                fleet(name, [str(a["id"])] if "id" in a else [])
            elif name == "shot":
                path = (target() / "mcp-shot.png").resolve()
                execute(Command("shot", (str(path),)))
                if path.stat().st_size > MAX_MESSAGE_BYTES * 3 // 4 - 8192:
                    raise NoemuError("message_too_large", "screenshot exceeds MCP response budget; use the CLI artifact path")
                return ToolOutput([{"type": "image", "data": base64.b64encode(path.read_bytes()).decode(), "mimeType": "image/png"}], result)
            else:
                if name == "network" and ((a.get("state") == "slow") != ("delay_ms" in a)):
                    raise NoemuError("invalid_argument", "delay_ms is required only for network slow")
                arguments = {"tap": (a.get("target", ""),), "type": (a.get("target", ""), a.get("text", "")),
                             "scroll": (a.get("direction", "down"),) + ((a["target"],) if "target" in a else ()),
                             "rotate": (str(a["count"]),) if "count" in a else (),
                             "crawl": (str(a.get("steps", 200)), str(a.get("minutes", 10))),
                             "wait": (str(a.get("ms", 1000)),),
                             "open": (a.get("target", ""),),
                             "network": (a.get("state", ""),) + ((str(a.get("delay_ms", 0)),) if a.get("state") == "slow" else ())}
                print(execute(Command(name, arguments.get(name, ()))))
    except SystemExit as e:
        raise RuntimeError(str(e.code) if e.code not in (None, 0) else out.getvalue()) from None
    finally:
        INSTANCE = previous
    return ToolOutput([{"type": "text", "text": out.getvalue().strip()}], result)


def mcp() -> int:
    from noemu.mcp_server import run
    return run()


def main(argv: list[str] | None = None) -> int:
    global INSTANCE
    argv = sys.argv[1:] if argv is None else argv
    INSTANCE = os.environ.get("NOEMU_ID") or None
    if len(argv) >= 2 and argv[0] == "--id":
        INSTANCE, argv = argv[1], argv[2:]
    if INSTANCE is not None:
        instance_path(INSTANCE)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if rest[:1] in (["-h"], ["--help"]):  # `noemu build --help` must not start a build
        print(__doc__)
        return 0
    if cmd in ("mcp", "stop") and rest:
        raise NoemuError("invalid_argument", f"{cmd} takes no arguments")
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
    if cmd in ("screen", "tap", "type", "scroll", "back", "rotate", "restore-activity", "kill", "leaks", "wait", "open", "network", "shot", "logs",
               "crawl"):
        if cmd == "shot" and rest:
            rest = [str(Path(rest[0]).resolve()), *rest[1:]]
        command = Command(cmd, tuple(rest))
        print(send(command, timeout=command.timeout_seconds))
        return 0
    print(__doc__)
    return 2


def entry() -> int:
    """The installed `noemu` command."""
    try:
        return main()
    except BrokenPipeError:  # the reader went away (`noemu screen | head`): not an error
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error) if str(error).startswith("noemu:") else f"noemu: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(entry())
