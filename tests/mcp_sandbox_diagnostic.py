"""Diagnostic only: a timer permits worker callbacks in socket-restricted sandboxes.

Run with the Python interpreter containing the installed NoEmu wheel. This changes
server scheduling and MUST NOT count as a passing installed MCP integration gate.
The required gate remains `python tests/mcp_stdio.py` without this launcher.
"""
from pathlib import Path
import runpy
import subprocess
import sys
from unittest import mock

LAUNCHER = """
import anyio
from noemu.mcp_server import serve

async def heartbeat():
    while True:
        await anyio.sleep(0.01)

async def main():
    async with anyio.create_task_group() as group:
        group.start_soon(heartbeat)
        await serve()
        group.cancel_scope.cancel()

anyio.run(main)
"""


if __name__ == "__main__":
    print("DIAGNOSTIC ONLY: timer-assisted MCP; does not satisfy the integration gate.", flush=True)
    original_popen = subprocess.Popen

    def launch(args, **kwargs):
        if args != [sys.executable, "-m", "noemu", "mcp"]:
            raise AssertionError(f"Unexpected subprocess: {args!r}")
        return original_popen([sys.executable, "-c", LAUNCHER], **kwargs)

    with mock.patch.object(subprocess, "Popen", launch):
        runpy.run_path(str(Path(__file__).with_name("mcp_stdio.py")), run_name="__main__")
