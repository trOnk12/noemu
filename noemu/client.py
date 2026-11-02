"""Talks to the per-user supervisor, starting it when needed."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from noemu import generations, supervisor
from noemu.protocol import NoemuError


def connect(root: Path) -> socket.socket:
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(str(root / "supervisor.sock"))
    return connection


def exchange(root: Path, request: dict, timeout: float) -> dict:
    with connect(root) as connection:
        connection.settimeout(timeout)
        connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                raise NoemuError("connection_lost", "the supervisor closed the connection; outcome unknown")
            data.extend(chunk)
            if len(data) > supervisor.MAX_FRAME_BYTES:
                raise NoemuError("message_too_large", "supervisor reply exceeds its limit")
    return json.loads(data)


def start(root: Path) -> None:
    with open(root / "supervisor.log", "a") as log:
        subprocess.Popen([sys.executable, "-m", "noemu", "_supervisor"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=log, start_new_session=True, cwd=root)


def running(root: Path) -> bool:
    """True when a supervisor of this exact code answers. An older one is replaced; its instances are adopted."""
    deadline = time.monotonic() + 10
    started = False
    while time.monotonic() < deadline:
        try:
            hello = exchange(root, {"op": "hello"}, 5)["result"]
            if hello["code"] == supervisor.code_identity():
                return True
            exchange(root, {"op": "shutdown"}, 5)
            started = False
        except (FileNotFoundError, ConnectionRefusedError):
            if not started:
                start(root)
                started = True
        except (socket.timeout, TimeoutError, ConnectionResetError, BrokenPipeError):
            pass  # A supervisor is exiting or busy. Nothing was asked of it yet: ask again.
        except NoemuError as error:
            if error.code != "connection_lost":
                raise
        time.sleep(0.02)
    raise NoemuError("supervisor_unavailable", f"no supervisor answered; see {root / 'supervisor.log'}")


def call(op: str, timeout: float = 120, **arguments):
    """One supervisor operation. Its failures are NoemuError with the supervisor's stable code."""
    root = generations.home()
    running(root)
    try:
        reply = exchange(root, {"op": op, **arguments}, timeout)
    except (socket.timeout, TimeoutError):
        raise NoemuError("deadline_exceeded", f"no answer from the supervisor in {timeout:g} s; outcome unknown") from None
    except OSError as error:
        raise NoemuError("connection_lost", f"{error}; outcome unknown; inspect `noemu status`") from error
    if not reply.get("ok"):
        raise NoemuError(reply.get("code", "protocol_error"), reply.get("message", "malformed supervisor reply"))
    return reply["result"]


def project() -> str:
    return str(Path(os.getcwd()).resolve())
