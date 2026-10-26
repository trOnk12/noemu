import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from noemu import cli


class CliTest(unittest.TestCase):
    def test_help(self):
        self.assertEqual(cli.main(["--help"]), 0)

    def test_unknown_command(self):
        self.assertEqual(cli.main(["fly"]), 2)

    def test_type_keeps_spaces_in_target_and_text(self):
        with mock.patch.object(cli, "send", return_value="ok") as send:
            cli.main(["type", "Text input", "hello world"])
        send.assert_called_once_with("type Text input\thello world", timeout=120)

    def test_crawl_gets_a_long_timeout(self):
        with mock.patch.object(cli, "send", return_value="ok") as send:
            cli.main(["crawl", "100"])
        send.assert_called_once_with("crawl 100", timeout=3600)

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
        (d / "pid").write_text(str(p.pid))
        (d / "state").write_text(state)
        (d / "build").write_text(build or cli.build_id())
        (d / "port").write_text(port)
        return d

    def test_session_commands_go_to_the_chosen_instance(self):
        self.instance("3", "running", port="4333")
        with mock.patch.object(cli.socket, "create_connection", side_effect=OSError("stop")) as connect:
            for argv, env in ((["--id", "3", "screen"], {}), (["screen"], {"NOEMU_ID": "3"})):
                with mock.patch.dict(os.environ, env), self.assertRaises(OSError):
                    cli.main(argv)
                self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 4333))

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

    def test_spawn_without_a_build_says_what_to_do(self):
        (cli.STATE / "launch.json").unlink()
        with self.assertRaises(SystemExit) as e:
            cli.main(["spawn"])
        self.assertIn("noemu build", str(e.exception))


if __name__ == "__main__":
    unittest.main()
