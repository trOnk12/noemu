"""Byte framing regressions, independent of SDK dispatch and worker scheduling."""
import io
import unittest
from unittest import mock

import anyio

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
