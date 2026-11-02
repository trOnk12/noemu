#!/usr/bin/env python3
"""Retain bounded fixture evidence before later checks overwrite it or cleanup runs."""
import hashlib
import json
from pathlib import Path
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 500
PATTERNS = (
    "fixtures/.gradle/noemu/*.log",
    "fixtures/.gradle/noemu/crash.txt",
    "fixtures/.gradle/noemu-fleet/*/log.txt",
    "fixtures/.gradle/noemu-fleet/*/crash.txt",
    "fixtures/.gradle/noemu-fleet/*/crawl-report.txt",
    "fixtures/*/build/test-results/**/*.xml",
    # The supervisor's state directory (NOEMU_HOME), collected as a separate source.
    "supervisor.log",
    "instances/*/log.txt",
    "instances/*/crash.txt",
    "instances/*/crawl-report.txt",
)


def collect(source: Path, destination: Path) -> bool:
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"complete": True, "files": [], "errors": [], "limits": {
        "file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES, "files": MAX_FILES}}
    remaining = MAX_TOTAL_BYTES
    seen = set()
    for pattern in PATTERNS:
        for path in sorted(source.glob(pattern)):
            relative = path.relative_to(source)
            if relative in seen:
                continue
            seen.add(relative)
            if len(seen) > MAX_FILES:
                manifest["errors"].append("artifact file-count limit exceeded")
                break
            try:
                if any(p.is_symlink() for p in (path, *path.parents) if p != source and source in p.parents):
                    raise ValueError("symlink artifact refused")
                if not path.is_file():
                    raise ValueError("artifact is not a regular file")
                if remaining <= 0:
                    raise ValueError("artifact total-byte limit exceeded")
                budget = min(MAX_FILE_BYTES, remaining)
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - budget))
                    data = stream.read(budget)
                truncated = size > len(data)
                stored = Path(str(relative) + (".truncated" if truncated else ""))
                target = destination / stored
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                remaining -= len(data)
                manifest["files"].append({"source": str(relative), "stored": str(stored),
                                          "source_bytes": size, "stored_bytes": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest(), "truncated": truncated})
                if truncated:
                    manifest["errors"].append(f"{relative}: only the final {len(data)} bytes retained")
            except (OSError, ValueError) as error:
                manifest["errors"].append(f"{relative}: {error}")
        if len(seen) > MAX_FILES:
            break
    manifest["complete"] = not manifest["errors"]
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest["complete"]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: gate_artifacts.py SOURCE RESULTS")
    sys.exit(0 if collect(Path(sys.argv[1]), Path(sys.argv[2])) else 1)
