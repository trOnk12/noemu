"""Byte framing regressions, independent of SDK dispatch and worker scheduling."""
import io
import json
import unittest
from unittest import mock

import anyio
from mcp import types

from noemu import mcp_server


class BoundedInputTest(unittest.TestCase):
    def read_lines(self, data, limit=64):
        async def direct_read(function, *args):
            # Test byte framing synchronously; installed stdio integration covers
            # real threads. The restricted sandbox blocks asyncio socket wakeup.
            return function(*args)

        async def read():
            reader = mcp_server.BoundedInput(io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))
            return [line async for line in reader]

        with mock.patch.object(mcp_server, "MAX_MESSAGE_BYTES", limit), \
                mock.patch.object(anyio.to_thread, "run_sync", direct_read):
            return anyio.run(read)

    def test_invalid_utf8_is_rejected_and_next_request_survives(self):
        self.assertEqual(self.read_lines(b'{"text":"\xff"}\n{"id":2}\n'), ["{\n", '{"id":2}\n'])

    def test_oversized_line_is_drained_without_losing_next_request(self):
        self.assertEqual(self.read_lines(b"x" * 1000 + b'\n{"id":2}\n'), ["{\n", '{"id":2}\n'])

    def test_limit_counts_utf8_bytes_and_includes_delimiter(self):
        self.assertEqual(self.read_lines(("ż" * 31 + " \n").encode()), ["ż" * 31 + " \n"])
        self.assertEqual(self.read_lines(("ż" * 32 + "\n").encode()), ["{\n"])

    def test_oversized_unterminated_input_reaches_eof(self):
        self.assertEqual(self.read_lines(b"x" * 1000), ["{\n"])


class BoundedOutputTest(unittest.TestCase):
    def write_messages(self, messages, limit=512):
        output = []
        class Sink:
            async def write(self, value):
                output.append(value)
            async def flush(self):
                pass
        async def write():
            writer = mcp_server.BoundedOutput(Sink())
            for message in messages:
                await writer.write(message)
                await writer.flush()
        with mock.patch.object(mcp_server, "MAX_MESSAGE_BYTES", limit):
            anyio.run(write)
        return output

    def test_oversized_evidence_is_an_error_and_next_reply_survives(self):
        large = json.dumps({"jsonrpc": "2.0", "id": "request-1", "result": {
            "content": [{"type": "text", "text": "x" * 1000}],
            "structuredContent": {"status": "passed"}}}) + "\n"
        following = '{"jsonrpc":"2.0","id":2,"result":{}}\n'
        output = self.write_messages([large, following])
        error = json.loads(output[0])
        self.assertEqual(error["id"], "request-1")
        self.assertEqual(error["error"]["data"]["code"], "message_too_large")
        self.assertEqual(error["error"]["data"]["status"], "inconclusive")
        self.assertNotIn("result", error)
        self.assertEqual(output[1], following)
        for message in output:
            self.assertLessEqual(len(message.encode()), 512)
            types.JSONRPCMessage.model_validate_json(message)

    def test_size_uses_encoded_bytes_not_character_count(self):
        message = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"text": "ż" * 300}}, ensure_ascii=False) + "\n"
        self.assertLess(len(message), 512)
        self.assertIn("error", json.loads(self.write_messages([message])[0]))

    def test_huge_request_identity_cannot_make_error_unbounded(self):
        message = json.dumps({"jsonrpc": "2.0", "id": "r" * 1000, "result": {}}) + "\n"
        output, = self.write_messages([message])
        self.assertLessEqual(len(output.encode()), 512)
        self.assertIsNone(json.loads(output)["id"])
