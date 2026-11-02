"""Evidence retention checks use temporary files, not Android execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("gate_artifacts", Path(__file__).resolve().parents[1] / "scripts/gate_artifacts.py")
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


class GateArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "src"
        self.output = self.root / "evidence"
        self.source.mkdir()

    def put(self, path, data=b"evidence"):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_text())

    def test_selected_logs_and_xml_keep_content_and_hashes(self):
        paths = ["fixtures/.gradle/noemu/build.log", "fixtures/.gradle/noemu-fleet/1/log.txt",
                 "fixtures/views/build/test-results/testDebugUnitTest/TEST-example.xml"]
        for path in paths:
            self.put(path)
        self.put("fixtures/.gradle/noemu-fleet/1/tmp/private-app-data", b"not an artifact")
        self.assertTrue(artifacts.collect(self.source, self.output))
        evidence = self.manifest()
        self.assertTrue(evidence["complete"])
        self.assertEqual({f["source"] for f in evidence["files"]}, set(paths))
        for item in evidence["files"]:
            self.assertEqual((self.output / item["stored"]).read_bytes(), b"evidence")
            self.assertEqual(item["sha256"], hashlib.sha256(b"evidence").hexdigest())
            self.assertFalse(item["truncated"])

    def test_size_limits_retain_tails_and_mark_evidence_incomplete(self):
        self.put("fixtures/.gradle/noemu/a.log", b"0123456789")
        self.put("fixtures/.gradle/noemu/b.log", b"abcdefghij")
        with mock.patch.object(artifacts, "MAX_FILE_BYTES", 4), mock.patch.object(artifacts, "MAX_TOTAL_BYTES", 6):
            self.assertFalse(artifacts.collect(self.source, self.output))
        evidence = self.manifest()
        self.assertFalse(evidence["complete"])
        self.assertEqual(sum(f["stored_bytes"] for f in evidence["files"]), 6)
        self.assertEqual((self.output / "fixtures/.gradle/noemu/a.log.truncated").read_bytes(), b"6789")
        self.assertTrue(all(f["truncated"] for f in evidence["files"]))

    def test_symlink_files_and_parent_directories_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "log.txt").write_text("private data")
        logs = self.source / "fixtures/.gradle/noemu"
        logs.mkdir(parents=True)
        (logs / "escape.log").symlink_to(outside / "log.txt")
        fleet = self.source / "fixtures/.gradle/noemu-fleet"
        fleet.mkdir()
        (fleet / "1").symlink_to(outside, target_is_directory=True)
        self.assertFalse(artifacts.collect(self.source, self.output))
        evidence = self.manifest()
        self.assertEqual(evidence["files"], [])
        self.assertEqual(len(evidence["errors"]), 2)
        self.assertNotIn("private data", json.dumps(evidence))

    def test_file_count_limit_is_an_explicit_failure(self):
        for i in range(3):
            self.put(f"fixtures/.gradle/noemu/{i}.log")
        with mock.patch.object(artifacts, "MAX_FILES", 2):
            self.assertFalse(artifacts.collect(self.source, self.output))
        self.assertEqual(len(self.manifest()["files"]), 2)
        self.assertIn("artifact file-count limit exceeded", self.manifest()["errors"])


if __name__ == "__main__":
    unittest.main()
