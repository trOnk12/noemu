import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from noemu import cli
from noemu.protocol import Result
from tests.test_protocol import reply as driver_reply


class CliTest(unittest.TestCase):
    def test_help(self):
        self.assertEqual(cli.main(["--help"]), 0)

    def test_help_after_a_command_prints_usage_and_runs_nothing(self):
        for cmd in ("build", "start", "spawn", "tap"):
            with mock.patch.object(cli, "fleet") as fleet, mock.patch.object(cli, "start") as start, \
                    mock.patch.object(cli, "send") as send, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([cmd, "--help"]), 0)
            fleet.assert_not_called(); start.assert_not_called(); send.assert_not_called()

    def test_unknown_command(self):
        self.assertEqual(cli.main(["fly"]), 2)

    def test_type_keeps_spaces_in_target_and_text(self):
        with mock.patch.object(cli, "send", return_value="ok") as send:
            cli.main(["type", "Text input", "hello world"])
        send.assert_called_once_with(cli.Command("type", ("Text input", "hello world")), timeout=120)

    def test_crawl_gets_a_long_timeout(self):
        with mock.patch.object(cli, "send", return_value="ok") as send:
            cli.main(["crawl", "100"])
        send.assert_called_once_with(cli.Command("crawl", ("100",)), timeout=3600)

    def test_start_needs_a_gradle_project(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                with self.assertRaises(SystemExit) as e:
                    cli.main(["start"])
                self.assertIn("gradlew", str(e.exception))
            finally:
                os.chdir(cwd)

    def test_driver_and_init_script_ship_with_the_package(self):
        self.assertTrue((cli.HERE / "noemu.init.gradle").exists())
        self.assertTrue((cli.HERE / "driver" / "NoemuDriver.kt").exists())
        # Robolectric finds RealClock through this file; without it every instance boots at the same uptime, in 1970.
        service = cli.HERE / "driver" / "resources" / "META-INF" / "services" / "org.robolectric.pluginapi.TestEnvironmentLifecyclePlugin"
        self.assertEqual(service.read_text().split(), ["dev.noemu.RealClock", "dev.noemu.NetworkPlugin"])
        self.assertTrue((cli.HERE / "driver" / "RealClock.kt").exists())


class FleetTest(unittest.TestCase):
    """The fleet's bookkeeping, with sleeping processes standing in for app JVMs."""

    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.TemporaryDirectory()
        os.chdir(self.dir.name)
        cli.STATE.mkdir(parents=True)
        (cli.STATE / "launch.json").write_text("{}")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        os.chdir(self.cwd)
        self.dir.cleanup()

    def instance(self, name, state, build=None, port="4000"):
        d = cli.FLEET / name
        d.mkdir(parents=True)
        p = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.procs.append(p)
        cli.processes.remember(d, p.pid)
        (d / "state").write_text(state)
        (d / "build").write_text(build or cli.build_id())
        (d / "port").write_text(port)
        return d

    def test_session_commands_go_to_the_chosen_instance(self):
        self.instance("3", "running", port="4333")
        with mock.patch.object(cli.socket, "create_connection", side_effect=OSError("stop")) as connect:
            for argv, env in ((["--id", "3", "screen"], {}), (["screen"], {"NOEMU_ID": "3"})):
                with mock.patch.dict(os.environ, env), self.assertRaises(cli.NoemuError):
                    cli.main(argv)
                self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 4333))

    def test_a_frozen_instance_is_refused_at_once_not_waited_on(self):
        self.instance("4", "pooled")
        self.instance("5", "asleep")
        with mock.patch.object(cli.socket, "create_connection") as connect:
            for name, hint in (("4", "noemu spawn"), ("5", "noemu wake 5")):
                with self.assertRaises(cli.NoemuError) as e:
                    cli.main(["--id", name, "screen"])
                self.assertIn(hint, str(e.exception))
        connect.assert_not_called()

    def test_no_answer_is_a_message_not_a_traceback(self):
        self.instance("6", "running")
        with mock.patch.object(cli.socket, "create_connection", side_effect=TimeoutError("timed out")):
            with self.assertRaises(cli.NoemuError) as e:
                cli.main(["--id", "6", "screen"])
        self.assertIn("no answer", str(e.exception))

    def test_spawn_takes_a_pooled_instance_of_the_current_build(self):
        self.instance("0", "pooled", build="old")
        self.instance("1", "pooled", port="4111")
        with mock.patch.object(cli.subprocess, "Popen") as refill, mock.patch("builtins.print") as out:
            cli.main(["spawn"])
        out.assert_called_once_with("1 4111")
        self.assertEqual(cli.state_of(cli.FLEET / "1"), "running")
        self.assertIn("_refill", refill.call_args.args[0])

    def test_two_agents_never_get_the_same_instance(self):
        d = self.instance("0", "pooled")
        self.assertTrue(cli.claim(d))
        self.assertFalse(cli.claim(d))

    def test_destroy_all_ends_every_instance(self):
        for name in ("0", "1"):
            self.instance(name, "running")
        with mock.patch("builtins.print"):
            cli.main(["destroy", "all"])
        for p in self.procs:
            p.wait(timeout=5)
        self.assertEqual(cli.instances(), [])

    def test_destroy_removes_the_instance_s_temporary_app_data(self):
        d = self.instance("0", "running")
        data = d / "tmp" / "robolectric-NoemuDriver_serve1" / "com.x-dataDir"
        data.mkdir(parents=True)
        (d / "datadir").write_text(str(data))
        with mock.patch("builtins.print"):
            cli.main(["destroy", "0"])
        self.assertFalse(data.parent.exists())

    def test_spawn_without_a_build_says_what_to_do(self):
        (cli.STATE / "launch.json").unlink()
        with self.assertRaises(SystemExit) as e:
            cli.main(["spawn"])
        self.assertIn("noemu build", str(e.exception))


class DoctorTest(unittest.TestCase):
    def test_it_names_what_will_stand_in_the_way(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                Path("gradlew").write_text("")
                Path("app").mkdir()
                Path("app/build.gradle.kts").write_text(
                    'plugins { id("com.android.application") }\nandroid { productFlavors { create("dev") } }\n'
                    "dependencies { implementation(libs.core.crypto.android) }\n")
                out = io.StringIO()
                with mock.patch.dict(os.environ, {"ANDROID_HOME": d}), contextlib.redirect_stdout(out):
                    code = cli.main(["doctor"])
            finally:
                os.chdir(cwd)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("ok   app module :app", text)
        self.assertIn("test<Flavor>DebugUnitTest", text)
        self.assertIn("NOEMU_DEPS=com.wire:core-crypto-jvm", text)

    def test_no_gradlew_fails(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["doctor"]), 1)
            finally:
                os.chdir(cwd)


class McpTest(unittest.TestCase):
    """The MCP server's protocol, with the tools' work mocked."""

    def serve(self, *messages):
        import anyio
        from contextlib import asynccontextmanager
        from mcp import types
        from mcp.shared.message import SessionMessage
        from noemu import mcp_server

        async def session():
            to_server, server_read = anyio.create_memory_object_stream(10)
            server_write, from_server = anyio.create_memory_object_stream(10)
            @asynccontextmanager
            async def transport(**kwargs):
                yield server_read, server_write
            init = {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
            queue = list(messages)
            if isinstance(queue[0], dict) and queue[0].get("method") == "initialize":
                queue[0]["params"].update(init["params"])
            else:
                queue = [init, {"jsonrpc": "2.0", "method": "notifications/initialized"}, *queue]
            replies = []
            with mock.patch.object(mcp_server, "stdio_server", transport):
                async with anyio.create_task_group() as tg:
                    # The managed sandbox blocks asyncio's cross-thread socket
                    # wakeup; a test-only timer lets completed worker callbacks run.
                    async def tick():
                        while True:
                            await anyio.sleep(0.01)
                    tg.start_soon(tick)
                    tg.start_soon(mcp_server.serve)
                    for message in queue:
                        try:
                            parsed = types.JSONRPCMessage.model_validate_json(message) if isinstance(message, str) else types.JSONRPCMessage.model_validate(message)
                        except Exception as error:
                            await to_server.send(error)
                            continue
                        await to_server.send(SessionMessage(parsed))
                        if isinstance(message, dict) and "id" in message:
                            with anyio.fail_after(5):
                                while True:
                                    reply = (await from_server.receive()).message.model_dump(by_alias=True, exclude_none=True)
                                    if reply.get("id") == message["id"]:
                                        break
                            if message["id"] != "init":
                                replies.append(reply)
                    tg.cancel_scope.cancel()
            await to_server.aclose()
            await server_read.aclose()
            await server_write.aclose()
            await from_server.aclose()
            return replies
        return anyio.run(session)

    def test_initialize_and_list_tools(self):
        replies = self.serve({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                             {"jsonrpc": "2.0", "method": "notifications/initialized"},
                             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(replies), 2)  # no answer to a notification
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "noemu")
        names = [t["name"] for t in replies[1]["result"]["tools"]]
        self.assertTrue({"spawn", "screen", "tap", "type", "destroy", "build"} <= set(names))

    def test_a_tool_call_goes_to_its_instance(self):
        result = Result(driver_reply(text="activity Main\n[0] button OK"))
        with mock.patch.object(cli, "request", return_value=result) as request:
            reply, = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "tap", "arguments": {"id": "4", "target": "OK"}}})
        request.assert_called_once_with(cli.Command("tap", ("OK",)), timeout=120)
        self.assertIsNone(cli.INSTANCE)
        self.assertIn("[0] button OK", reply["result"]["content"][0]["text"])
        self.assertEqual(reply["result"]["structuredContent"], result.data)

    def test_open_takes_an_activity_or_a_link(self):
        with mock.patch.object(cli, "request", return_value=Result(driver_reply(text="activity Chat"))) as request:
            self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "open", "arguments": {"id": "2", "target": "wire://conversation/1"}}})
        request.assert_called_once_with(cli.Command("open", ("wire://conversation/1",)), timeout=120)

    def test_network_switches_airplane_mode(self):
        with mock.patch.object(cli, "request", return_value=Result(driver_reply(text="network off"))) as request:
            self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "network", "arguments": {"id": "2", "state": "off"}}})
        request.assert_called_once_with(cli.Command("network", ("off",)), timeout=120)
        with mock.patch.object(cli, "request", return_value=Result(driver_reply(text="network delay 300 ms"))) as request:
            self.serve({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "network", "arguments": {"id": "2", "state": "slow", "delay_ms": 300}}})
        request.assert_called_once_with(cli.Command("network", ("slow", "300")), timeout=120)

    def test_supported_driver_options_are_available_over_mcp(self):
        cases = [("scroll", {"direction": "right", "target": "tag:horizontal"}, ("right", "tag:horizontal"), 120),
                 ("rotate", {"count": 2}, ("2",), 120),
                 ("restore-activity", {}, (), 120), ("leaks", {}, (), 900),
                 ("crawl", {"steps": 5, "minutes": 1}, ("5", "1"), 3600)]
        for name, arguments, expected, timeout in cases:
            with self.subTest(name=name), mock.patch.object(cli, "request", return_value=Result(driver_reply())) as request:
                response, = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": name, "arguments": {"id": "2", **arguments}}})
                self.assertFalse(response["result"].get("isError", False), response)
                request.assert_called_once_with(cli.Command(name, expected), timeout=timeout)

    def test_invalid_network_options_do_not_silently_default(self):
        for arguments in ({"state": "slow"}, {"state": "off", "delay_ms": 300}):
            with self.subTest(arguments=arguments), mock.patch.object(cli, "request") as request:
                response, = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "network", "arguments": {"id": "2", **arguments}}})
                self.assertTrue(response["result"]["isError"])
                request.assert_not_called()

    def test_a_failing_tool_is_a_tool_error_not_a_crash(self):
        reply, = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "screen", "arguments": {"id": "999"}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("isn't running", reply["result"]["content"][0]["text"])

    def test_malformed_messages_do_not_end_the_session(self):
        for raw in ("{", "[]", "null", '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":[]}'):
            with self.subTest(raw=raw):
                reply, = self.serve(raw, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
                self.assertEqual(reply["result"], {})

    def test_invalid_tool_arguments_do_not_reach_driver(self):
        for arguments in ({"id": "../escape", "target": "OK"}, {"id": "1"},
                          {"id": "1", "target": 7}, {"id": "1", "target": "OK", "extra": True}):
            with self.subTest(arguments=arguments), mock.patch.object(cli, "request") as request:
                reply, = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "tap", "arguments": arguments}})
            self.assertTrue(reply["result"]["isError"])
            request.assert_not_called()

    def test_driver_failure_is_a_tool_error(self):
        result = Result(driver_reply(status="failed", error={"code": "disabled_target", "message": "disabled"}))
        with mock.patch.object(cli, "request", return_value=result):
            reply, = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "tap", "arguments": {"id": "1", "target": "Disabled"}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("disabled_target", reply["result"]["content"][0]["text"])
        self.assertEqual(reply["result"]["structuredContent"], result.data)

    def test_unknown_method(self):
        reply, = self.serve({"jsonrpc": "2.0", "id": 7, "method": "foo/bar"})
        self.assertEqual(reply["error"]["code"], -32602)  # official SDK rejects unknown request models


if __name__ == "__main__":
    unittest.main()
