import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from noemu import cli
from noemu.protocol import Command, MAX_MESSAGE_BYTES, NoemuError, Result


def reply(request_id="request", **changes):
    value = {"version": 1, "request_id": request_id,
             "identity": {"instance": None, "build": None, "profile": "legacy-sdk36"},
             "status": "passed", "error": None, "text": "ends with dots...\n",
             "observation": None, "artifacts": [], "capabilities": [], "limitations": [],
             "timing": {"duration_ms": 1}}
    return {**value, **changes}


class CommandTest(unittest.TestCase):
    def test_multiline_unicode_tabs_and_trailing_spaces_survive(self):
        command = Command("type", ("Text\tinput", "one\n.\ntwo\r\nzażółć\t  "))
        wire = command.encode("request", 10)
        self.assertEqual(wire.count(b"\n"), 1)
        self.assertEqual(json.loads(wire)["args"], list(command.args))
        with mock.patch.object(cli, "send", return_value="ok") as send, contextlib.redirect_stdout(io.StringIO()):
            cli.main(["type", *command.args])
        self.assertEqual(send.call_args.args[0], command)

    def test_invalid_commands_fail_before_io(self):
        cases = [("tap", ()), ("tap", ("",)), ("type", ("a",)), ("screen", ("extra",)),
                 ("wait", ("-1",)), ("wait", ("nan",)), ("rotate", ("0",)), ("crawl", ("1", "999")),
                 ("scroll", ("diagonal",)), ("network", ("slow",)), ("network", ("on", "9")),
                 ("network", ("slow", "-1")), ("unknown", ())]
        for name, args in cases:
            with self.subTest(name=name, args=args), self.assertRaises(NoemuError):
                Command(name, args)

    def test_request_and_timeout_limits(self):
        with self.assertRaises(NoemuError):
            Command("type", ("field", "a" * MAX_MESSAGE_BYTES)).encode("request", 10)
        for timeout in (0, -1, float("nan"), float("inf"), 3601):
            with self.subTest(timeout=timeout), self.assertRaises(NoemuError):
                Command("screen").encode("request", timeout)


class ResultTest(unittest.TestCase):
    def test_failure_is_never_success(self):
        for status in ("failed", "unsupported", "inconclusive"):
            value = reply(status=status, error={"code": "example_failure", "message": "did not complete"})
            result = Result.decode(json.dumps(value).encode(), "request", None)
            with self.assertRaises(NoemuError) as error:
                result.text()
            self.assertEqual(error.exception.code, "example_failure")
            self.assertEqual(error.exception.result, value)

    def test_invalid_or_mismatched_results_are_rejected(self):
        for changes in ({"request_id": "other"}, {"version": 2}, {"version": True},
                        {"identity": {"instance": "1", "build": None, "profile": "legacy-sdk36"}},
                        {"status": "passed", "error": {"code": "bad", "message": "bad"}},
                        {"status": "failed"}, {"status": "almost"}, {"timing": {"duration_ms": -1}}):
            with self.subTest(changes=changes), self.assertRaises(NoemuError):
                Result.decode(json.dumps(reply(**changes)).encode(), "request", None)
        for data in (b"[]", b"null", b"{", b"\xff", b'{"version": NaN}'):
            with self.subTest(data=data), self.assertRaises(NoemuError):
                Result.decode(data, "request", None)


class TransportTest(unittest.TestCase):
    def run_transport(self, chunks, timeout=1):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "port").write_text("4000")
            socket = mock.MagicMock()
            socket.recv.side_effect = chunks
            with mock.patch.object(cli, "STATE", root), mock.patch.object(cli, "INSTANCE", None), \
                    mock.patch.object(cli.uuid, "uuid4", return_value="request"), \
                    mock.patch.object(cli.socket, "create_connection") as connect:
                connect.return_value.__enter__.return_value = socket
                result = cli.send(Command("screen"), timeout=timeout)
            return result, socket

    def test_fragmented_response_preserves_text(self):
        wire = (json.dumps(reply()) + "\n").encode()
        value, socket = self.run_transport([wire[:20], wire[20:]])
        self.assertEqual(value, "ends with dots...\n")
        request = json.loads(socket.sendall.call_args.args[0])
        self.assertEqual(request["command"], "screen")
        self.assertEqual(request["version"], 1)

    def test_partial_closed_and_oversized_responses_fail(self):
        for chunks in ([b"",], [b'{"version":1}', b""], [b"x" * (MAX_MESSAGE_BYTES + 1)]):
            with self.subTest(chunks_length=len(chunks)), self.assertRaises(NoemuError):
                self.run_transport(chunks)

    def test_absolute_deadline_limits_a_trickling_peer(self):
        with mock.patch.object(cli.time, "monotonic", side_effect=[0, .5, 1.1]):
            with self.assertRaises(NoemuError) as error:
                self.run_transport([b"{"], timeout=1)
        self.assertEqual(error.exception.code, "deadline_exceeded")

    def test_cli_exit_code_reports_driver_failure(self):
        with mock.patch.object(cli.sys, "argv", ["noemu", "tap", "Missing"]), \
                mock.patch.object(cli, "send", side_effect=NoemuError("target_not_found", "missing")), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cli.entry(), 1)
        self.assertIn("target_not_found", error.getvalue())
