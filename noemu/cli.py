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

Instances for parallel agents: one supervisor per OS user serves every project and worktree. Each build becomes an
immutable generation, and each instance keeps the generation it started from.

    noemu build [--module :app] [--task testDebugUnitTest]   # compile, snapshot, and make this the project's generation
    noemu acquire [--generation G] [--wait S] [--lease-seconds S] [--owner NAME] [--json]
                                 # an exclusive instance; prints `export NOEMU_LEASE=...`
    NOEMU_LEASE=... noemu screen # any command above, on that instance
    noemu release [ID]           # end it; a later acquire starts clean
    noemu sleep [ID] | noemu wake [ID]   # freeze / resume its JVM: no CPU, memory and state kept
    noemu status [--json] | noemu gc [--json]   # instances, limits, memory; remove what retention no longer needs
    noemu pool N                 # keep N warm instances of this project's build
    noemu spawn [--no-refill]    # a shared instance, used by ID; prints "ID PORT"
    noemu --id ID screen         # any command on a shared instance (or NOEMU_ID=ID)
    noemu destroy ID|all         # end a shared instance, or all of this project's
    noemu list                   # instances: id, state, port, memory
    noemu shutdown               # stop the supervisor and warm instances; leased ones are adopted by the next
    noemu mcp                    # the same as an MCP server (stdio) for coding agents
    noemu doctor [--module :app] [--task ...]   # what will stand in the way (reads the project, runs nothing)

NOEMU_GRADLE_ARGS adds arguments to the Gradle command (e.g. an init script for a mirror). NOEMU_SETTLE_MS (default
4000) caps how long an action waits for the screen to settle (changing numbers, like a clock, don't count).
NOEMU_DEPS adds test-runtime dependencies (comma-separated), e.g. JVM builds of native libraries the app loads.
NOEMU_SHOTS=0 skips the screenshot after every action (about 0.3 s each; `shot` still makes one). NOEMU_XMX (default 1g)
is each fleet instance's heap; NOEMU_JVM_ARGS (default "-XX:+UseSerialGC -XX:TieredStopAtLevel=1": smaller, faster to
start) sets each instance's JVM options. NOEMU_LEASE_SECONDS (default 1800) ends a lease without actions.
NOEMU_MAX_ACTIVE, NOEMU_MAX_JVMS, NOEMU_MAX_COLD, NOEMU_WARM and NOEMU_MEMORY_MIB set the supervisor's limits when it starts.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from noemu import processes, transport
from noemu.protocol import Command, Result, NoemuError, MAX_MESSAGE_BYTES

HERE = Path(__file__).resolve().parent
# In the project's .gradle folder: ignored by git in Android projects, and no build or clean deletes it.
STATE = Path(".gradle/noemu")
INSTANCE: str | None = None  # shared instance the session commands go to (--id or NOEMU_ID)
LEASE: str | None = None  # exclusive lease the session commands go to (NOEMU_LEASE); it names its instance


def instance_id(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"0|[1-9][0-9]{0,9}", name):
        sys.exit("noemu: invalid instance id; expected a nonnegative decimal number")
    return name


def supervised(op: str, timeout: float = 120, **arguments):
    from noemu import client
    return client.call(op, timeout, **arguments)


def project() -> str:
    return str(Path.cwd().resolve())


def request(command: Command, timeout: float = 120) -> Result:
    request_id = str(uuid.uuid4())
    payload = command.encode(request_id, timeout)
    if LEASE or INSTANCE is not None:
        reply = supervised("act", timeout + 15, lease=LEASE, instance=INSTANCE, payload=payload.decode("utf-8"), limit=timeout)
        return Result.decode(reply["reply"].encode("utf-8"), request_id, LEASE.split(".")[0] if LEASE else INSTANCE)
    port_file = STATE / "port"
    if not port_file.exists():
        raise NoemuError("not_running", "not running here; `noemu start` first")
    try:
        port = int(port_file.read_text())
    except (OSError, ValueError) as error:
        raise NoemuError("invalid_state", f"cannot read driver port: {error}") from error
    if not 1 <= port <= 65535:
        raise NoemuError("invalid_state", "invalid driver port")
    return Result.decode(transport.exchange(port, payload, timeout), request_id, None)


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
    (STATE / "port").unlink(missing_ok=True)
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
    published = supervised("publish", 600, project=project(), module=opts["--module"], task=opts["--task"], spec=str(spec.resolve()))
    print(f"noemu: built ({time.time() - t0:.0f}s); generation {published['generation']} is this project's current build")
    return 0


def flags(args: list[str], allowed: dict[str, type], switches: tuple[str, ...] = ()) -> dict:
    """Parses --name value options and bare switches. Anything else fails."""
    found: dict = {}
    it = iter(args)
    for argument in it:
        if argument in switches:
            found[argument] = True
        elif argument in allowed:
            value = next(it, None)
            if value is None:
                raise NoemuError("invalid_argument", f"{argument} requires a value")
            try:
                found[argument] = allowed[argument](value)
            except ValueError:
                raise NoemuError("invalid_argument", f"invalid value for {argument}") from None
        else:
            raise NoemuError("invalid_argument", f"unexpected argument {argument}")
    return found


def acquire(args: list[str]) -> int:
    opts = flags(args, {"--generation": str, "--wait": float, "--lease-seconds": float, "--owner": str}, ("--json",))
    wait = opts.get("--wait", 600.0)
    granted = supervised("acquire", wait + 30, project=project(), generation=opts.get("--generation"), wait=wait,
                         ttl=opts.get("--lease-seconds"), owner=opts.get("--owner", ""))
    if "--json" in opts:
        print(json.dumps(granted))
    else:
        print(f"export NOEMU_LEASE={granted['lease']}  # instance {granted['instance']}, generation {granted['generation']}")
    return 0


def spawn(refill: bool = True) -> int:
    granted = supervised("acquire", 630, project=project(), shared=True, refill=refill, wait=600)
    print(f"{granted['instance']} {granted['port']}")
    return 0


def selected(rest: list[str], cmd: str) -> dict:
    """The instance a lifecycle command names: an explicit ID, or the session's lease or ID."""
    if len(rest) > 1:
        raise NoemuError("invalid_argument", f"{cmd} accepts one instance id")
    if rest:
        return {"instance": instance_id(rest[0]), "lease": LEASE if LEASE and LEASE.split(".")[0] == rest[0] else None}
    if LEASE or INSTANCE is not None:
        return {"instance": INSTANCE, "lease": LEASE}
    sys.exit(f"noemu: {cmd} needs an instance id or NOEMU_LEASE (noemu status)")


def memory(instance: dict) -> str:
    size = instance["resident_mib"]
    return "memory unknown" if size is None else f"{size:.1f} MiB"


def status(json_output: bool) -> int:
    report = supervised("status")
    if json_output:
        print(json.dumps(report, indent=1))
        return 0
    limits, used = report["limits"], report["memory"]
    print(f"{report['active']}/{limits['max_active']} active, {len(report['instances'])} instances, {report['queued']} queued, "
          f"{used['charged_mib']}/{used['budget_mib']} MiB")
    for instance in report["instances"]:
        print(f"{instance['id']}\t{instance['state']}\t{'exclusive' if instance['exclusive'] else 'shared'}\t"
              f"generation {instance['generation']}\t{memory(instance)}" + (f"\t{instance['error']}" if instance["error"] else ""))
    return 0


def fleet(cmd: str, rest: list[str]) -> int:
    if cmd == "build":
        return build(rest)
    if cmd == "acquire":
        return acquire(rest)
    if cmd == "shutdown":
        supervised("shutdown")
        return 0
    if cmd == "status":
        return status("--json" in flags(rest, {}, ("--json",)))
    if cmd == "gc":
        opts = flags(rest, {}, ("--json",))
        removed = supervised("gc", 600)
        print(json.dumps(removed) if "--json" in opts else ", ".join(f"{count} {name}" for name, count in removed.items()) + " removed")
        return 0
    if cmd == "pool":
        if len(rest) > 1:
            raise NoemuError("invalid_argument", "pool accepts one size")
        size = Command.integer(rest[0], 0, 4) if rest else 1
        result = supervised("pool", 630, project=project(), size=size, wait=600)
        print(f"{len(result['warm'])} of {result['requested']} warm: {' '.join(result['warm']) or '-'} ({result['pressure']})")
        return 0 if len(result["warm"]) >= size else 1
    if cmd == "spawn":
        if rest not in ([], ["--no-refill"]):
            raise NoemuError("invalid_argument", "spawn accepts only --no-refill")
        return spawn(refill=not rest)
    if cmd == "list":
        if rest:
            raise NoemuError("invalid_argument", "list takes no arguments")
        for instance in supervised("status")["instances"]:
            print(f"{instance['id']}\t{instance['state']}\tport {instance['port'] or '-'}\t{memory(instance)}")
        return 0
    if cmd == "destroy" and rest == ["all"]:
        here = [i for i in supervised("status")["instances"]
                if i["project"] == project() and not i["exclusive"] and i["state"] in ("leased", "asleep")]
        for instance in here:
            supervised("release", instance=instance["id"])
            print(f"{instance['id']} destroyed")
        return 0
    if cmd in ("destroy", "release"):
        if cmd == "destroy" and not rest:
            sys.exit("noemu: destroy needs an instance id (noemu list)")
        target = selected(rest, cmd)
        print(f"{supervised('release', 30, **target)['instance']} destroyed")
        return 0
    wait = 600
    result = supervised(cmd, wait + 30, wait=wait, **selected(rest, cmd))
    print(f"{result['instance']} {'asleep' if cmd == 'sleep' else 'awake'}")
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

ID = {"id": {"type": "string", "pattern": "^(0|[1-9][0-9]{0,9})$", "description": "instance id, from acquire or spawn"},
      "lease": {"type": "string", "description": "the lease from acquire; required for exclusive instances"}}
TOOLS = {
    "acquire": ("Lease an exclusive app instance of this project's current build, queueing within the fleet's limits. "
                "Pass the returned id and lease to other tools. Call build first after changing code.",
                {"generation": {"type": "string", "description": "an exact generation instead of the current one"},
                 "wait": {"type": "number", "minimum": 0, "maximum": 3600},
                 "lease_seconds": {"type": "number", "minimum": 1, "maximum": 86400}}, []),
    "release": ("End an instance. Its data is destroyed; a later acquire starts clean.", ID, ["id"]),
    "status": ("Instances, generations, limits and memory across every project of this user.", {}, []),
    "gc": ("Remove generations and logs that retention no longer needs.", {}, []),
    "spawn": ("Get a running shared app instance, used by id alone. Prefer acquire when several agents share a machine. "
              "Returns its id and first screen. Call build first after changing code.", {}, []),
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
    "sleep": ("Freeze the instance (no CPU, memory and state kept).", ID, ["id"]),
    "wake": ("Resume a frozen instance; waits for an active slot.", ID, ["id"]),
    "destroy": ("End the instance and remove its files.", ID, ["id"]),
    "list": ("All instances: id, state, port, memory.", {}, []),
    "pool": ("Keep N warm instances of this project's build, so later acquisitions are fast.",
             {"n": {"type": "integer", "minimum": 0, "maximum": 4}}, ["n"]),
    "build": ("Compile the app and publish it as an immutable generation (after every code change; running instances "
              "keep the generation they started from).", {"module": {"type": "string"}, "task": {"type": "string"}}, []),
}


def run_tool(name: str, a: dict):
    """Runs one tool through the same functions as the command line; returns MCP content."""
    import contextlib, io, base64
    from noemu.protocol import ToolOutput
    global INSTANCE, LEASE
    out = io.StringIO()
    result = None
    def execute(command: Command) -> str:
        nonlocal result
        result = request(command, timeout=command.timeout_seconds)
        return result.text()
    if name not in TOOLS:
        raise NoemuError("unsupported", f"unknown tool {name}")
    previous = INSTANCE, LEASE
    INSTANCE, LEASE = a.get("id"), a.get("lease")
    try:
        if INSTANCE is not None:
            instance_id(INSTANCE)
        with contextlib.redirect_stdout(out):
            if name == "acquire":
                wait = float(a.get("wait", 600))
                print(json.dumps(supervised("acquire", wait + 30, project=project(), generation=a.get("generation"), wait=wait,
                                            ttl=a.get("lease_seconds"), owner="mcp")))
            elif name == "release":
                supervised("release", 30, instance=INSTANCE, lease=LEASE)
                print(f"{INSTANCE} destroyed")
            elif name == "status":
                print(json.dumps(supervised("status")))
            elif name == "gc":
                print(json.dumps(supervised("gc", 600)))
            elif name == "spawn":
                spawn()
                INSTANCE = out.getvalue().split()[0]
                print("\n" + execute(Command("screen")))
            elif name == "build":
                build([x for k in ("module", "task") if a.get(k) for x in (f"--{k}", a[k])])
            elif name == "pool":
                if fleet("pool", [str(int(a["n"]))]):
                    raise NoemuError("capacity_timeout", out.getvalue())
            elif name in ("list", "sleep", "wake", "destroy"):
                fleet(name, [str(a["id"])] if "id" in a else [])
            elif name == "shot":
                with tempfile.TemporaryDirectory(prefix="noemu-shot-") as directory:
                    path = Path(directory) / "screen.png"
                    execute(Command("shot", (str(path),)))
                    if path.stat().st_size > MAX_MESSAGE_BYTES * 3 // 4 - 8192:
                        raise NoemuError("message_too_large", "screenshot exceeds MCP response budget; use the CLI artifact path")
                    image = base64.b64encode(path.read_bytes()).decode()
                return ToolOutput([{"type": "image", "data": image, "mimeType": "image/png"}], result)
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
        INSTANCE, LEASE = previous
    return ToolOutput([{"type": "text", "text": out.getvalue().strip()}], result)


def mcp() -> int:
    from noemu.mcp_server import run
    return run()


def main(argv: list[str] | None = None) -> int:
    global INSTANCE, LEASE
    argv = sys.argv[1:] if argv is None else argv
    INSTANCE = os.environ.get("NOEMU_ID") or None
    LEASE = os.environ.get("NOEMU_LEASE") or None
    if len(argv) >= 2 and argv[0] == "--id":
        INSTANCE, LEASE, argv = argv[1], None, argv[2:]
    if INSTANCE is not None:
        instance_id(INSTANCE)
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
    if cmd == "_supervisor":
        from noemu import supervisor
        return supervisor.serve()
    if cmd == "doctor":
        return doctor(rest)
    if cmd == "start":
        return start(rest)
    if cmd == "stop":
        return stop()
    if cmd in ("build", "acquire", "release", "status", "gc", "pool", "spawn", "list", "sleep", "wake", "destroy", "shutdown"):
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
