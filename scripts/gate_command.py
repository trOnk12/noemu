#!/usr/bin/env python3
"""Bound a POSIX gate command and terminate its process group on timeout/cancel."""
import argparse
import math
import os
import signal
import subprocess
import sys
import time


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise argparse.ArgumentTypeError("seconds must be finite and in (0, 86400]")
    return seconds


def run(command, seconds, grace):
    interrupted = 0

    def cancel(signum, _frame):
        nonlocal interrupted
        interrupted = interrupted or signum

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    process = None
    try:
        deadline = time.monotonic() + seconds
        process = subprocess.Popen(command, start_new_session=True)
        while True:
            if interrupted:
                result = 128 + interrupted
                print(f"gate: command interrupted by signal {interrupted}", file=sys.stderr, flush=True)
                break
            status = process.poll()
            if status is not None:
                return status if status >= 0 else 128 - status
            if time.monotonic() >= deadline:
                result = 124
                print(f"gate: deadline exceeded ({seconds:g}s)", file=sys.stderr, flush=True)
                break
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

        # Keep the leader unreaped until group cleanup finishes to pin its PID.
        # Descendants may ignore TERM even when the command's leader exits.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                time.sleep(grace)
        process.wait()
        return result
    except OSError as error:
        print(f"gate: command could not run: {error}", file=sys.stderr, flush=True)
        return 125
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", required=True, type=positive_seconds)
    parser.add_argument("--grace", default=5, type=positive_seconds)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required")
    return run(command, args.seconds, args.grace)


if __name__ == "__main__":
    sys.exit(main())
