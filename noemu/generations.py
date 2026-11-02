"""Immutable runtime generations: everything one build needs to run, copied out of the project.

A generation is identified by the hash of its contents, and appears under its final name by an atomic rename, read-only.
Later builds, failed builds and `gradle clean` cannot change what a running instance loads. Dependency jars live once in a
content-addressed store and are hard-linked into each generation.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

TEST_CONFIG = Path("com/android/tools/test_config.properties")
# Paths in the Android Gradle plugin's unit test configuration, relative to the module directory.
RESOURCE_KEYS = ("android_merged_assets", "android_merged_manifest", "android_resource_apk")
# Per-instance JVM settings; the supervisor chooses these.
INSTANCE_ARGS = ("-Dnoemu.out=", "-Dnoemu.settle=", "-Dnoemu.shots=", "-Dnoemu.instance=", "-Xmx")


class GenerationError(RuntimeError):
    pass


def home() -> Path:
    """The private per-user state directory. Refuses one that another user could tamper with."""
    default = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "noemu"
    root = Path(os.environ.get("NOEMU_HOME") or default)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise GenerationError(f"noemu: {root} must be a directory private to this user")
    return root


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def store_blob(blobs: Path, source: Path) -> tuple[Path, str]:
    digest = hash_file(source)
    blob = blobs / digest
    if not blob.exists():
        temporary = blobs / f".tmp-{uuid.uuid4().hex}"
        shutil.copyfile(source, temporary)
        temporary.chmod(0o444)
        temporary.replace(blob)
    return blob, digest


def remove_tree(path: Path) -> None:
    """Removes a tree, including the read-only directories of a generation."""
    if path.is_symlink() or not path.exists():
        path.unlink(missing_ok=True)
        return
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path)


def freeze(path: Path) -> None:
    for directory, _, files in os.walk(path, topdown=False):
        for name in files:
            os.chmod(os.path.join(directory, name), 0o444)
        os.chmod(directory, 0o555)


def copy_entry(source: Path, target: Path, entries: dict[str, str], name: str) -> None:
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False)
        for directory, _, files in os.walk(target):
            for file in sorted(files):
                full = Path(directory) / file
                entries[f"{name}/{full.relative_to(target)}"] = hash_file(full)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entries[name] = hash_file(target)


def read_test_config(path: Path) -> list[tuple[str, str]]:
    return [tuple(line.split("=", 1)) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#")]


def publish(root: Path, project: Path, module: str, task: str, spec_path: Path) -> dict:
    """Snapshots the build recorded in spec_path; returns its manifest. Building the same contents again reuses it."""
    try:
        spec = json.loads(spec_path.read_text())
        java, jvm_args, classpath = spec["java"], spec["jvmArgs"], spec["classpath"]
        working = Path(spec["workingDir"]).resolve()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise GenerationError(f"noemu: unreadable launch record: {error}") from error
    project = project.resolve()
    generations, blobs = root / "generations", root / "blobs"
    generations.mkdir(exist_ok=True)
    blobs.mkdir(exist_ok=True)
    build = generations / f".tmp-{uuid.uuid4().hex}"
    (build / "cp").mkdir(parents=True)
    try:
        entries: dict[str, str] = {}
        relative: list[str] = []
        config: Path | None = None
        for index, raw in enumerate(classpath.split(os.pathsep)):
            source = Path(raw)
            if not source.exists():  # Gradle lists output directories the build never created.
                continue
            name = f"cp/{index:03d}-{source.name}"
            if source.is_file() and not inside(source.resolve(), project):
                blob, digest = store_blob(blobs, source)
                os.link(blob, build / name)
                entries[name] = digest
            else:
                copy_entry(source, build / name, entries, name)
                if (build / name / TEST_CONFIG).is_file():
                    config = build / name / TEST_CONFIG
            relative.append(name)

        lines = read_test_config(config) if config else []
        resources: dict[str, str] = {}
        for key, value in lines:
            if key not in RESOURCE_KEYS:
                continue
            source = (working / value).resolve()
            if not source.exists():
                raise GenerationError(f"noemu: {key} is missing from the build: {source}")
            resources[key] = f"resources/{key}/{source.name}"
            copy_entry(source, build / resources[key], entries, resources[key])

        kept = [a for a in jvm_args if not a.startswith(INSTANCE_ARGS)]
        unsnapshotted = [a for a in kept if str(project) in a]
        if unsnapshotted:
            raise GenerationError(f"noemu: JVM arguments refer into the project, which a snapshot would not cover: {unsnapshotted}")
        identity = hashlib.sha256(json.dumps({"java": java, "args": kept, "entries": entries}, sort_keys=True).encode())
        generation = identity.hexdigest()[:16]
        final = generations / generation
        if final.exists():
            remove_tree(build)
            return json.loads((final / "manifest.json").read_text())

        if config:
            # Only now is the final path known. The hash above covers the resources this points at.
            rewritten = [(k, str(final / resources[k]) if k in resources else v) for k, v in lines]
            config.write_text("".join(f"{k}={v}\n" for k, v in rewritten))
        manifest = {"id": generation, "project": str(project), "module": module, "task": task, "java": java,
                    "jvm_args": kept, "classpath": [str(final / name) for name in relative]}
        (build / "manifest.json").write_text(json.dumps(manifest, indent=1))
        freeze(build)
        try:
            build.replace(final)
        except OSError:  # Published concurrently with identical contents.
            if not (final / "manifest.json").exists():
                raise
            remove_tree(build)
        return manifest
    except BaseException:
        remove_tree(build)
        raise


def collect(root: Path, keep: set[str]) -> dict:
    """Removes generations outside keep, abandoned partial builds, and blobs nothing links to."""
    removed = {"generations": 0, "blobs": 0}
    for path in (root / "generations").glob("*") if (root / "generations").exists() else ():
        if path.name.startswith(".tmp-") or path.name not in keep:
            remove_tree(path)
            removed["generations"] += not path.name.startswith(".tmp-")
    for blob in (root / "blobs").glob("*") if (root / "blobs").exists() else ():
        if blob.name.startswith(".tmp-") or blob.stat().st_nlink == 1:
            blob.unlink()
            removed["blobs"] += not blob.name.startswith(".tmp-")
    return removed
