"""Installed MCP stdio smoke check, run in CI outside the source directory."""
import json
import select
import subprocess
import sys
import tempfile
import unittest


class InstalledStdioTest(unittest.TestCase):
    def test_bad_input_does_not_prevent_next_request(self):
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, "-m", "noemu", "mcp"], cwd=directory,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, bufsize=1)
            try:
                def send(value):
                    process.stdin.write((value if isinstance(value, str) else json.dumps(value)) + "\n")
                    process.stdin.flush()

                def response(request_id):
                    # Read one line at a time from the unbuffered binary descriptor
                    # to avoid TextIOWrapper read-ahead hiding pending responses.
                    import os, time
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        line = bytearray()
                        while not line.endswith(b"\n"):
                            ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
                            self.assertTrue(ready, "MCP response deadline exceeded")
                            chunk = os.read(process.stdout.fileno(), 1)
                            self.assertTrue(chunk, "MCP server exited prematurely")
                            line.extend(chunk)
                        value = json.loads(line)
                        if value.get("id") == request_id:
                            return value
                    self.fail("MCP response deadline exceeded")

                send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "noemu-install-check", "version": "1"}}})
                self.assertEqual(response(1)["result"]["serverInfo"]["name"], "noemu")
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                for malformed in ("{", "[]", "null"):
                    send(malformed)
                # A malformed byte sequence must not kill the transport or be
                # silently replaced inside a tool argument.
                process.stdin.buffer.write(b'{"text":"\xff"}\n')
                process.stdin.buffer.flush()
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                self.assertIn("tap", {tool["name"] for tool in response(2)["result"]["tools"]})
                send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "tap", "arguments": {"id": "../escape", "target": "OK"}}})
                self.assertTrue(response(3)["result"]["isError"])
                send({"jsonrpc": "2.0", "id": 4, "method": "ping"})
                self.assertEqual(response(4)["result"], {})
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
