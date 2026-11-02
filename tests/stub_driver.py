#!/usr/bin/env python3
"""Stands in for an app JVM in supervisor tests: a real process speaking the real driver protocol.

Launched as `java`. It reads the -D options the supervisor passes and serves until told to quit. In `slow <ms>`, `tap`
is held that long while the number of overlapping requests is recorded, and `hang` never answers. `crash` exits.
"""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path


def option(name):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"-D{name}=")), None)


out = Path(option("noemu.out"))
instance = option("noemu.instance")
build = (out / "build").read_text()
marker = Path(option("java.io.tmpdir")) / "data"
if os.environ.get("STUB_FAIL_START"):
    sys.exit(3)
time.sleep(float(os.environ.get("STUB_START_SECONDS", "0")))
started = time.time()
server = socket.create_server(("127.0.0.1", 0))
temporary = out / "port.partial"
temporary.write_text(str(server.getsockname()[1]))
temporary.replace(out / "port")
overlap, peak = 0, 0
lock = threading.Lock()


def reply(request, text, status="passed", error=None):
    return {"version": 1, "request_id": request["request_id"], "identity": {"instance": instance, "build": build, "profile": "stub"},
            "status": status, "error": error, "text": text, "observation": None, "artifacts": [],
            "timing": {"duration_ms": 0}, "capabilities": [], "limitations": []}


def serve(connection):
    global overlap, peak
    with connection:
        request = json.loads(connection.makefile().readline())
        name, args = request["command"], request["args"]
        with lock:
            overlap += 1
            peak = max(peak, overlap)
        if name == "wait" and args == ["120000"]:
            time.sleep(3600)
        if name == "wait":
            time.sleep(int(args[0]) / 1000)
        with lock:
            overlap -= 1
        if name == "type":  # App data: visible to this instance only.
            marker.write_text(args[1])
        text = {"logs": str(peak), "screen": marker.read_text() if marker.exists() else "clean",
                "leaks": f"{os.getcwd()} {time.time() - started:.3f}"}.get(name, name)
        connection.sendall(json.dumps(reply(request, text)).encode() + b"\n")
        if name == "quit":
            os._exit(0)
        if name == "back":
            os._exit(9)


while True:
    connection, _ = server.accept()
    threading.Thread(target=serve, args=(connection,), daemon=True).start()
