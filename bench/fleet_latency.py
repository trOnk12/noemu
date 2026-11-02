#!/usr/bin/env python3
"""Measures fleet operations against a real app, separately, and writes every sample.

    NOEMU_HOME=$(mktemp -d) python bench/fleet_latency.py --project fixtures --module :views --cycles 100 --out DIR

Each cycle waits until a warm instance is ready (not timed), then times: warm acquisition, the first usable observation,
one semantic action, sleep, wake to a usable observation, and release with confirmed process exit. Build time and the
cold start that refills the pool are recorded separately. Any failed operation ends the run as incomplete.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psutil

from noemu import cli, client
from noemu.protocol import Command


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))]


def timed(samples: dict, name: str, operation):
    start = time.perf_counter()
    result = operation()
    samples.setdefault(name, []).append((time.perf_counter() - start) * 1000)
    return result


def observe(lease: str, name="screen", *args):
    cli.LEASE, cli.INSTANCE = lease, None
    result = cli.request(Command(name, tuple(args)))
    if result.data["status"] != "passed":
        raise RuntimeError(f"{name} did not pass: {result.data['error']}")
    return result


def warm_ready(project: str) -> float:
    start = time.perf_counter()
    while not any(i["state"] == "warm" for i in client.call("status")["instances"]):
        if time.perf_counter() - start > 300:
            raise RuntimeError("no warm instance after 300 s")
        time.sleep(0.05)
    return (time.perf_counter() - start) * 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--module", default=":views")
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not os.environ.get("NOEMU_HOME"):
        sys.exit("set NOEMU_HOME to a private directory")
    project = str(Path(args.project).resolve())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    os.chdir(project)
    os.environ["NOEMU_SHOTS"] = "0"
    samples: dict[str, list[float]] = {}
    record = {"host": {"platform": platform.platform(), "cpus": os.cpu_count(),
                       "memory_gib": round(psutil.virtual_memory().total / 2**30, 1), "python": sys.version.split()[0]},
              "cycles_requested": args.cycles, "complete": False}
    try:
        build = timed(samples, "build_ms", lambda: subprocess.run(
            [sys.executable, "-m", "noemu", "build", "--module", args.module], capture_output=True, text=True, check=True))
        record["build_output"] = build.stdout.strip().splitlines()[-1]
        timed(samples, "pool_first_warm_ms", lambda: client.call("pool", 630, project=project, size=1, wait=600))
        for cycle in range(args.cycles):
            samples.setdefault("refill_cold_start_ms", []).append(warm_ready(project))
            grant = timed(samples, "warm_acquire_ms", lambda: client.call("acquire", 60, project=project, wait=30))
            timed(samples, "first_observation_ms", lambda: observe(grant["lease"]))
            timed(samples, "semantic_action_ms", lambda: observe(grant["lease"], "tap", "Change target"))
            timed(samples, "sleep_ms", lambda: client.call("sleep", lease=grant["lease"]))
            timed(samples, "wake_to_observation_ms", lambda: (client.call("wake", lease=grant["lease"]), observe(grant["lease"])))
            pid = int((Path(os.environ["NOEMU_HOME"]) / "instances" / grant["instance"] / "pid").read_text())
            timed(samples, "release_confirmed_ms", lambda: client.call("release", lease=grant["lease"]))
            if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                raise RuntimeError(f"process {pid} outlived its release")
            record["cycles_completed"] = cycle + 1
        record["complete"] = True
    except Exception as error:  # Recorded, never hidden: an incomplete run is a result too.
        record["error"] = repr(error)
    finally:
        client.call("pool", 60, project=project, size=0, wait=5)
        record["summary"] = {name: {"n": len(v), "p50": round(statistics.median(v), 1), "p95": round(percentile(v, 0.95), 1),
                                    "max": round(max(v), 1)} for name, v in samples.items()}
        (out / "samples.json").write_text(json.dumps(samples))
        (out / "summary.json").write_text(json.dumps(record, indent=1))
        print(json.dumps(record, indent=1))
    return 0 if record["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
