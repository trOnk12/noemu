"""Verified process ownership for lifecycle commands; never signal a bare PID."""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import psutil


class ProcessIdentityError(RuntimeError):
    pass


def start_ticks(pid: int) -> int | None:
    """Linux: the kernel's start time in clock ticks since boot. It never changes for a process.

    psutil's create_time() adds this to the boot time in /proc/stat, which is wall-clock time minus uptime: it moves
    when the clock is stepped or slewed (NTP, a VM syncing with its host), and one process then reads differently.
    """
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def remember(directory: Path, pid: int) -> None:
    process = psutil.Process(pid)
    identity = {"pid": pid, "created": process.create_time(), "start_ticks": start_ticks(pid), "uid": process.uids().real}
    temporary = directory / "process.json.tmp"
    temporary.write_text(json.dumps(identity))
    temporary.replace(directory / "process.json")
    (directory / "pid").write_text(str(pid))


def verified(directory: Path) -> psutil.Process:
    try:
        identity = json.loads((directory / "process.json").read_text())
        pid = identity["pid"]
        if type(pid) is not int or pid <= 1 or identity["uid"] != os.getuid():
            raise ValueError("invalid process owner")
        if int((directory / "pid").read_text()) != pid:
            raise ValueError("PID file changed")
        process = psutil.Process(pid)
        ticks = identity.get("start_ticks")
        same_start = start_ticks(pid) == ticks if ticks is not None else process.create_time() == identity["created"]
        if not same_start or process.uids().real != identity["uid"]:
            raise ValueError("process identity changed")
        if process.status() == psutil.STATUS_ZOMBIE:
            raise psutil.NoSuchProcess(pid)
        return process
    except (OSError, ValueError, KeyError, TypeError, psutil.Error) as error:
        raise ProcessIdentityError(f"noemu: cannot verify process for {directory.name}: {error}") from error


def alive(directory: Path) -> bool:
    try:
        verified(directory)
        return True
    except ProcessIdentityError:
        return False


def resident_memory(directory: Path) -> int | None:
    """Resident bytes for the verified process, or unknown when it cannot be read."""
    try:
        return verified(directory).memory_info().rss
    except (ProcessIdentityError, OSError, psutil.Error):
        return None


def send_signal(directory: Path, sig: int) -> None:
    process = verified(directory)
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        # Pin the kernel process before the final identity check. PID reuse after
        # that check cannot redirect the signal to another process.
        fd = os.pidfd_open(process.pid)
        try:
            verified(directory)
            signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)
    else:
        # psutil also checks creation time before signaling on macOS.
        process.send_signal(sig)


def terminate(directory: Path, timeout: float = 5) -> None:
    process = verified(directory)
    send_signal(directory, signal.SIGKILL)
    deadline = time.monotonic() + timeout
    while True:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"noemu: process {process.pid} has not exited; cleanup deferred")
        time.sleep(0.01)
