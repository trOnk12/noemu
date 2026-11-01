import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from noemu import cli, processes


class InstanceSafetyTest(unittest.TestCase):
    def test_untrusted_ids_are_rejected_before_io(self):
        for name in ("../outside", "/tmp/outside", "1/../../outside", "..", "-1", "١", "01"):
            for args in (["--id", name, "screen"], ["destroy", name], ["sleep", name], ["wake", name]):
                with self.subTest(args=args), mock.patch.object(cli, "send") as send, \
                        mock.patch.object(cli.os, "killpg") as kill, self.assertRaises(SystemExit):
                    cli.main(args)
                send.assert_not_called()
                kill.assert_not_called()

    def test_environment_id_is_validated(self):
        with mock.patch.dict(os.environ, {"NOEMU_ID": "../escape"}), self.assertRaises(SystemExit):
            cli.main(["screen"])

    def test_symlink_instance_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fleet").mkdir()
            (root / "outside").mkdir()
            (root / "fleet/0").symlink_to(root / "outside", target_is_directory=True)
            with mock.patch.object(cli, "FLEET", root / "fleet"), self.assertRaises(SystemExit):
                cli.main(["destroy", "0"])


class ProcessSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.process = subprocess.Popen(["sleep", "60"], start_new_session=True)

    def tearDown(self):
        self.process.kill()
        self.process.wait()
        self.temp.cleanup()

    def test_pid_alone_cannot_authorize_a_signal(self):
        (self.directory / "pid").write_text(str(self.process.pid))
        with self.assertRaises(processes.ProcessIdentityError):
            processes.terminate(self.directory)
        self.assertIsNone(self.process.poll())

    def test_reused_pid_is_not_signaled(self):
        processes.remember(self.directory, self.process.pid)
        path = self.directory / "process.json"
        identity = json.loads(path.read_text())
        identity["created"] -= 10
        path.write_text(json.dumps(identity))
        with self.assertRaises(processes.ProcessIdentityError):
            processes.terminate(self.directory)
        self.assertIsNone(self.process.poll())

    def test_destroy_waits_for_exit_and_does_not_delete_unowned_data(self):
        processes.remember(self.directory, self.process.pid)
        with tempfile.TemporaryDirectory(prefix="robolectric-unowned-") as other:
            marker = Path(other) / "keep"
            marker.write_text("another instance")
            (self.directory / "datadir").write_text(other)
            (self.directory / "tmp").mkdir()
            cli.destroy_one(self.directory)
            self.process.wait(timeout=1)
            self.assertTrue(marker.exists())
            self.assertFalse((self.directory / "tmp").exists())
            self.assertEqual(cli.state_of(self.directory), "destroyed")


class ArgumentsTest(unittest.TestCase):
    def test_rejected_build_preserves_recorded_launch_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "launch.json"
            log = root / "build.log"
            spec.write_text("recorded launch")
            log.write_text("previous build")
            with mock.patch.object(cli, "STATE", root), mock.patch.object(cli, "gradle") as gradle, \
                    self.assertRaises(SystemExit):
                cli.build(["--typo", "value"])
            gradle.assert_not_called()
            self.assertEqual(spec.read_text(), "recorded launch")
            self.assertEqual(log.read_text(), "previous build")

    def test_bad_build_arguments_are_not_ignored(self):
        for args in (["--module"], ["--typo", "value"], ["unexpected"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                cli.options(args)
