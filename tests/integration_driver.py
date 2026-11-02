"""Required driver gate, run explicitly with a provisioned Android toolchain.

NOEMU_FIXTURE=views python tests/integration_driver.py
NOEMU_FIXTURE=compose python tests/integration_driver.py

Missing dependencies fail the run; this gate never silently skips fixtures.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from noemu import cli
from noemu.protocol import Command

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures"
MODULE = os.environ.get("NOEMU_FIXTURE", "views")


def command(*args, timeout=600):
    result = subprocess.run([sys.executable, "-m", "noemu", *args], cwd=PROJECT,
                            capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "NOEMU_SHOTS": "0"})
    if result.returncode:
        raise RuntimeError(f"noemu {args}: {result.stdout}\n{result.stderr}")
    return result.stdout


class DriverIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE not in ("views", "compose"):
            raise RuntimeError("NOEMU_FIXTURE must be views or compose")
        if not (PROJECT / "gradlew").exists():
            raise RuntimeError("fixture wrapper missing; see fixtures/README.md")
        command("build", "--module", f":{MODULE}", timeout=900)

    def setUp(self):
        self.old_cwd = Path.cwd()
        self.old_instance = cli.INSTANCE
        self.instance = command("spawn", "--no-refill").strip().split()[0]
        os.chdir(PROJECT)
        cli.INSTANCE = self.instance

    def tearDown(self):
        try:
            command("destroy", self.instance)
        finally:
            cli.INSTANCE = self.old_instance
            os.chdir(self.old_cwd)

    def act(self, name, *args):
        return cli.request(Command(name, tuple(args))).data

    def screen(self):
        result = self.act("screen")
        self.assertEqual(result["status"], "passed", result)
        return result["observation"]["elements"]

    def field(self, label):
        matches = [e for e in self.screen() if e["editable"] and label in e["label"]]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]["ref"]

    def test_disabled_ambiguous_and_missing_targets_fail(self):
        for target, code in (("Disabled", "disabled_target"), ("Duplicate", "ambiguous_target"),
                             ("Hidden", "target_not_found")):
            result = self.act("tap", target)
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(result["error"]["code"], code)

    def test_old_observation_reference_cannot_be_reused(self):
        target = next(e["ref"] for e in self.screen() if e["label"] == "Change target")
        self.screen()
        self.assertEqual(self.act("tap", target)["error"]["code"], "stale_target")

    def test_multiline_text_survives_wire_and_action(self):
        text = "one\n.\ntwo\tzażółć"
        result = self.act("type", self.field("Good input"), text)
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(any(text in e["label"] for e in result["observation"]["elements"]), result)

    def test_generated_ids_keep_references_without_inventing_resource_names(self):
        elements = self.screen()
        good, = [e for e in elements if e["editable"] and "Good input" in e["label"]]
        bad, = [e for e in elements if e["editable"] and "Broken input" in e["label"]]
        if MODULE == "views":
            self.assertIsNone(good["selector"])
            self.assertEqual(bad["selector"], "id:dev.noemu.fixture.views:id/broken_input")
        else:
            self.assertEqual(good["selector"], "tag:good")
            self.assertEqual(bad["selector"], "tag:bad")
        result = self.act("type", good["ref"], "generated-id-field")
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(any("generated-id-field" in e["label"] for e in result["observation"]["elements"]))

    def test_rotation_distinguishes_saved_and_broken_state(self):
        self.assertEqual(self.act("type", self.field("Good input"), "kept-token")["status"], "passed")
        self.assertEqual(self.act("type", self.field("Broken input"), "lost-token")["status"], "passed")
        result = self.act("rotate", "2")
        self.assertEqual(result["status"], "passed", result)
        labels = [e["label"] for e in result["observation"]["elements"]]
        self.assertTrue(any("kept-token" in label for label in labels), labels)
        self.assertFalse(any("lost-token" in label for label in labels), labels)

    def test_horizontal_scroll_changes_visible_items(self):
        before = [e["label"] for e in self.screen()]
        result = self.act("scroll", "right")
        self.assertEqual(result["status"], "passed", result)
        after = [e["label"] for e in result["observation"]["elements"]]
        self.assertNotEqual(before, after)

    def test_focus_crash_during_rotation_is_never_reported_as_success(self):
        opened = self.act("open", "dev.noemu.fixture.FocusCrashActivity")
        self.assertEqual(opened["status"], "passed", opened)
        self.assertTrue(any(e["label"] == "Focus crash fixture ready"
                            for e in opened["observation"]["elements"]), opened)
        result = self.act("rotate")
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["error"]["code"], "execution_error", result)
        self.assertIn("fixture focus crash after recreation", result["error"]["message"])

    def test_rotation_finishes_while_an_animation_never_ends(self):
        opened = self.act("open", "dev.noemu.fixture.EndlessAnimationActivity")
        self.assertEqual(opened["status"], "passed", opened)
        result = self.act("rotate", "2")
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(any(e["label"] == "Endless animation fixture ready"
                            for e in result["observation"]["elements"]), result)

    def test_crash_is_a_cli_failure(self):
        result = subprocess.run([sys.executable, "-m", "noemu", "--id", self.instance, "tap", "Crash"],
                                capture_output=True, text=True, timeout=120)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture deliberate crash", result.stderr)

    def test_activity_restore_does_not_claim_process_death(self):
        result = self.act("restore-activity")
        self.assertEqual(result["status"], "passed", result)
        self.assertIn("statics and singletons preserved", result["text"])
        self.assertNotIn("killed in the background", result["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
