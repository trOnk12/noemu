import contextlib
import copy
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

    def test_malformed_typed_inputs_fail_with_typed_errors(self):
        for name, args in (([], ()), ("tap", None), ("wait", ("9" * 5000,))):
            with self.subTest(name=name), self.assertRaises(NoemuError) as error:
                Command(name, args)
            self.assertEqual(error.exception.code, "invalid_argument")
        for request_id, timeout in (("", 1), ("bad\nid", 1), ("a" * 129, 1),
                                    ("request", True), ("request", "1"), ("request", 10 ** 1000)):
            with self.subTest(request_id=request_id, timeout=timeout), self.assertRaises(NoemuError):
                Command("screen").encode(request_id, timeout)


class ResultTest(unittest.TestCase):
    def test_observation_is_validated_before_targets_are_exposed(self):
        observation = {"id": "obs", "activity": "Main", "elements": [{
            "ref": "obs:0", "label": "Save", "kind": "button", "selector": "id:app:id/save",
            "enabled": True, "visible": True, "editable": False, "state": None, "bounds": [0, 0, 20, 30]}]}
        result = Result.decode(json.dumps(reply(observation=observation)).encode(), "request", None)
        self.assertEqual(result.data["observation"], observation)
        for change in ({"ref": "other:0"}, {"ref": "obs:1"}, {"enabled": "false"},
                       {"visible": 1}, {"editable": None}, {"label": []}, {"kind": ""},
                       {"selector": {}}, {"state": 0}, {"bounds": [0, 0, 0, 30]},
                       {"bounds": [False, 0, 20, 30]}):
            broken = copy.deepcopy(observation)
            broken["elements"][0].update(change)
            with self.subTest(change=change), self.assertRaises(NoemuError):
                Result.decode(json.dumps(reply(observation=broken)).encode(), "request", None)

    def test_malformed_evidence_cannot_be_accepted_as_success(self):
        for changes in ({"capabilities": [None]}, {"artifacts": [{}]}, {"limitations": [1]},
                        {"identity": {"instance": None, "build": None, "profile": ""}},
                        {"timing": {"duration_ms": 10 ** 1000}},
                        {"observation": {}}, {"observation": {"id": "obs", "activity": "Main", "elements": [None]}}):
            with self.subTest(changes=changes), self.assertRaises(NoemuError) as error:
                Result.decode(json.dumps(reply(**changes)).encode(), "request", None)
            self.assertEqual(error.exception.code, "protocol_error")

    def test_duplicate_json_fields_are_not_last_value_wins(self):
        data = json.dumps(reply()).replace('"status": "passed"', '"status": "failed", "status": "passed"')
        with self.assertRaises(NoemuError) as error:
            Result.decode(data.encode(), "request", None)
        self.assertEqual(error.exception.code, "protocol_error")

    def test_deeply_nested_json_is_a_protocol_error(self):
        with self.assertRaises(NoemuError) as error:
            Result.decode(b"[" * 2000 + b"0" + b"]" * 2000, "request", None)
        self.assertEqual(error.exception.code, "protocol_error")

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
    def run_transport(self, chunks, timeout=1, connection=None, command=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "port").write_text("4000")
            socket = connection if connection is not None else mock.MagicMock()
            socket.recv.side_effect = chunks
            with mock.patch.object(cli, "STATE", root), mock.patch.object(cli, "INSTANCE", None), \
                    mock.patch.object(cli.uuid, "uuid4", return_value="request"), \
                    mock.patch.object(cli.socket, "create_connection") as connect:
                connect.return_value.__enter__.return_value = socket
                result = cli.send(command or Command("screen"), timeout=timeout)
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
        with mock.patch.object(cli.time, "monotonic", side_effect=[0, .1, .5, 1.1]):
            with self.assertRaises(NoemuError) as error:
                self.run_transport([b"{"], timeout=1)
        self.assertEqual(error.exception.code, "deadline_exceeded")

    def test_connection_time_is_subtracted_before_sending(self):
        with mock.patch.object(cli.time, "monotonic", side_effect=[0, .75, .8]):
            _, socket = self.run_transport([(json.dumps(reply()) + "\n").encode()], timeout=1)
        self.assertAlmostEqual(socket.settimeout.call_args_list[0].args[0], .25)

    def test_expired_connection_budget_does_not_send_a_mutation(self):
        socket = mock.MagicMock()
        with mock.patch.object(cli.time, "monotonic", side_effect=[0, 1.1]):
            with self.assertRaises(NoemuError) as error:
                self.run_transport([], timeout=1, connection=socket, command=Command("tap", ("Save",)))
        self.assertEqual(error.exception.code, "deadline_exceeded")
        socket.sendall.assert_not_called()

    def test_cli_exit_code_reports_driver_failure(self):
        with mock.patch.object(cli.sys, "argv", ["noemu", "tap", "Missing"]), \
                mock.patch.object(cli, "send", side_effect=NoemuError("target_not_found", "missing")), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cli.entry(), 1)
        self.assertIn("target_not_found", error.getvalue())
