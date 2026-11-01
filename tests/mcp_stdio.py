"""Installed MCP stdio contract; run outside the checkout with the target Python."""
import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest


class InstalledStdioTest(unittest.TestCase):
    def test_bad_input_does_not_prevent_next_request(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen([sys.executable, "-m", "noemu", "mcp"], cwd=directory,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                       bufsize=0, env={**os.environ, "PYTHONIOENCODING": "ascii"})
            pending = bytearray()
            stage = "initialization"
            limit = 1024 * 1024
            try:
                os.set_blocking(process.stdin.fileno(), False)

                def send_bytes(data):
                    # A broken server must not hang the test while it fills a pipe.
                    deadline = time.monotonic() + 10
                    remaining = memoryview(data)
                    while remaining:
                        budget = deadline - time.monotonic()
                        self.assertGreater(budget, 0, "MCP request write deadline exceeded")
                        _, ready, _ = select.select([], [process.stdin], [], budget)
                        self.assertTrue(ready, "MCP request write deadline exceeded")
                        try:
                            count = os.write(process.stdin.fileno(), remaining[:65536])
                        except BlockingIOError:
                            continue
                        self.assertGreater(count, 0, "MCP request write made no progress")
                        remaining = remaining[count:]

                def send(value):
                    send_bytes(((value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + "\n").encode())

                def response(request_id):
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        while b"\n" not in pending:
                            self.assertLess(len(pending), limit, "MCP response exceeds 1 MiB")
                            ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
                            self.assertTrue(ready, "MCP response deadline exceeded")
                            chunk = os.read(process.stdout.fileno(), 65536)
                            self.assertTrue(chunk, "MCP server exited prematurely")
                            pending.extend(chunk)
                        line, _, rest = pending.partition(b"\n")
                        pending[:] = rest
                        self.assertLessEqual(len(line) + 1, limit, "MCP response exceeds 1 MiB")
                        value = json.loads(line.decode("utf-8"))
                        self.assertEqual(value["jsonrpc"], "2.0")
                        if value.get("id") == request_id:
                            return value
                        # The SDK reports malformed input through logging notifications
                        # or uncorrelated errors. Do not hide unexpected replies.
                        self.assertIsNone(value.get("id"))
                        if "error" not in value:
                            self.assertEqual(value.get("method"), "notifications/message")
                            self.assertEqual(value["params"]["level"], "error")
                    self.fail("MCP response deadline exceeded")

                send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "noemu-install-check", "version": "1"}}})
                self.assertEqual(response(1)["result"]["serverInfo"]["name"], "noemu")
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                stage = "malformed and oversized input recovery"
                for malformed in ("{", "[]", "null"):
                    send(malformed)
                send_bytes(b'{"text":"\xff"}\n')
                # Multiple framing chunks must be drained without losing the next line.
                send_bytes(b"x" * (2 * limit + 17) + b"\n")
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                self.assertIn("tap", {tool["name"] for tool in response(2)["result"]["tools"]})
                stage = "tool error and subsequent ping"
                send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "tap", "arguments": {"id": "../escape", "target": "OK"}}})
                self.assertTrue(response(3)["result"]["isError"])
                # MCP always uses UTF-8, even if a client inherits an ASCII locale.
                unicode_id = "ping-żółw-日本語-😀"
                send({"jsonrpc": "2.0", "id": unicode_id, "method": "ping"})
                self.assertEqual(response(unicode_id)["result"], {})
                stage = "clean stdin EOF shutdown"
                process.stdin.close()
                self.assertEqual(process.wait(timeout=5), 0)
            except Exception as error:
                stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, stderr.tell() - 8192))
                diagnostic = stderr.read().decode("utf-8", errors="replace")
                self.fail(f"{stage}: {error}\nChild exit: {process.poll()}\n"
                          f"Child stderr (last 8192 bytes):\n{diagnostic or '(empty)'}")
            finally:
                if process.poll() is None:
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
