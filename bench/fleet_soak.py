#!/usr/bin/env python3
"""Lifecycle soak: many acquire/act/release cycles by concurrent agents, then a search for leftovers.

    python bench/fleet_soak.py --cycles 1000 --agents 3 --out DIR

Instances are the test stub (tests/stub_driver.py), a real process speaking the driver protocol, so this exercises the
supervisor's scheduling, signalling and cleanup, not app startup. Each cycle checks that the new instance starts with no
data from earlier ones and writes its own. Afterwards no stub process, instance directory or lease may remain.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--agents", type=int, default=3)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="nmsoak"))
    home = base / "h"
    home.mkdir(mode=0o700)
    os.environ.update({"NOEMU_HOME": str(home), "NOEMU_WARM": "1", "NOEMU_RESERVE_MIB": "1", "NOEMU_MEMORY_MIB": "100000"})
    from noemu import client
    from noemu.protocol import Command
    from tests import test_supervisor as harness

    java = base / "java"
    java.write_text(f"#!/bin/sh\nexec {sys.executable} {harness.STUB} \"$@\"\n")
    java.chmod(0o755)
    fleet = harness.Fleet()
    fleet.base, fleet.java = base, java
    project = fleet.project("app")
    client.call("publish", project=str(project), module=":app", task="t", spec=str(project / "launch.json"))

    counter = iter(range(args.cycles))
    lock = threading.Lock()
    failures, leaks, peaks, durations = [], [], {"active": 0, "jvms": 0}, []

    def act(lease, name, *values):
        payload = Command(name, tuple(values)).encode("r", 10).decode()
        return json.loads(client.call("act", 30, lease=lease, payload=payload, limit=10)["reply"])["text"]

    def agent(number):
        while True:
            with lock:
                cycle = next(counter, None)
            if cycle is None:
                return
            try:
                start = time.perf_counter()
                grant = client.call("acquire", 120, project=str(project), wait=100)
                seen = act(grant["lease"], "screen")
                if seen != "clean":
                    leaks.append({"cycle": cycle, "instance": grant["instance"], "saw": seen})
                act(grant["lease"], "type", "field", f"agent {number} cycle {cycle}")
                client.call("release", lease=grant["lease"])
                durations.append((time.perf_counter() - start) * 1000)
            except Exception as error:
                failures.append({"cycle": cycle, "error": repr(error)})

    def watch(stop):
        while not stop.is_set():
            status = client.call("status")
            live = [i for i in status["instances"] if i["state"] != "failed"]
            peaks["active"] = max(peaks["active"], status["active"])
            peaks["jvms"] = max(peaks["jvms"], len(live))
            time.sleep(0.01)

    stop = threading.Event()
    watcher = threading.Thread(target=watch, args=(stop,))
    watcher.start()
    started = time.time()
    agents = [threading.Thread(target=agent, args=(n,)) for n in range(args.agents)]
    for thread in agents:
        thread.start()
    for thread in agents:
        thread.join()
    stop.set()
    watcher.join()
    elapsed = time.time() - started

    client.call("pool", project=str(project), size=0, wait=10)
    client.call("gc")
    remaining = client.call("status")["instances"]
    supervisor_pid = client.call("status")["pid"]
    client.call("shutdown")
    time.sleep(0.5)
    orphans = [p.pid for p in psutil.process_iter(["cmdline"]) if any(str(harness.STUB) in part for part in (p.info["cmdline"] or []))]
    directories = sorted(p.name for p in (home / "instances").iterdir())
    durations.sort()
    result = {"cycles": args.cycles, "agents": args.agents, "seconds": round(elapsed, 1), "failures": failures,
              "data_leaks": leaks, "peak_active": peaks["active"], "peak_jvms": peaks["jvms"],
              "remaining_instances": remaining, "orphan_processes": orphans, "instance_directories_left": directories,
              "supervisor_pid": supervisor_pid,
              "cycle_ms": {"p50": round(durations[len(durations) // 2], 1) if durations else None,
                           "p95": round(durations[int(len(durations) * 0.95)], 1) if durations else None}}
    result["passed"] = not (failures or leaks or remaining or orphans or directories) and peaks["active"] <= 2 and peaks["jvms"] <= 4
    from noemu import generations
    generations.remove_tree(base)  # Generations are read-only; plain removal would fail.
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "soak.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
