import os
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


if __name__ == "__main__":
    unittest.main()
