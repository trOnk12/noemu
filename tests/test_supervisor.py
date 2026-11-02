"""The supervisor against real processes and signals, with a stub standing in for each app JVM."""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import psutil

from noemu import cli, client, generations, supervisor
from noemu.protocol import Command, NoemuError

STUB = Path(__file__).with_name("stub_driver.py")


def wait_until(condition, timeout=10, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {message}")


class Fleet(unittest.TestCase):
    """One real supervisor process per test, in a private temporary home."""
    environment = {"NOEMU_WARM": "0"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nm")
        self.base = Path(self.tmp.name)
        self.root = self.base / "h"
        self.root.mkdir(mode=0o700)
        self.java = self.base / "java"
        self.java.write_text(f"#!/bin/sh\nexec {sys.executable} {STUB} \"$@\"\n")
        self.java.chmod(0o755)
        patch = mock.patch.dict(os.environ, {"NOEMU_HOME": str(self.root), "NOEMU_RESERVE_MIB": "1",
                                             "NOEMU_MEMORY_MIB": "100000", **self.environment})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.stop_everything)
        self.project_a = self.project("a")

    def stop_everything(self):
        try:
            pid = client.call("status")["pid"]
            os.kill(pid, signal.SIGKILL)
        except (RuntimeError, OSError):
            pass
        for directory in (self.root / "instances").glob("*"):
            try:
                cli.processes.terminate(directory)
            except (RuntimeError, TimeoutError):
                pass

    def project(self, name, contents="classes"):
        """A project with one build output and a Gradle test config, as `noemu build` leaves them."""
        project = self.base / name
        output = project / "build/classes"
        (output / "com/android/tools").mkdir(parents=True)
        (output / "App.class").write_text(contents)
        (output / "com/android/tools/test_config.properties").write_text("android_merged_manifest=build/AndroidManifest.xml\n")
        (project / "build/AndroidManifest.xml").write_text(f"<manifest>{contents}</manifest>")
        dependency = self.base / "dependency.jar"
        dependency.write_text("shared dependency")
        (project / "launch.json").write_text(json.dumps({
            "java": str(self.java), "jvmArgs": ["-Dnoemu.out=ignored", "-Xmx3g", "-ea"],
            "classpath": f"{output}:{dependency}:{project}/missing", "workingDir": str(project)}))
        return project

    def publish(self, project):
        return client.call("publish", project=str(project), module=":app", task="t", spec=str(project / "launch.json"))["generation"]

    def acquire(self, project=None, **arguments):
        arguments.setdefault("wait", 20)
        return client.call("acquire", 40, project=str(project or self.project_a), **arguments)

    def act(self, grant, name="screen", *args, limit=10.0):
        command = Command(name, tuple(args))
        payload = command.encode("request", limit).decode()
        reply = client.call("act", limit + 15, lease=grant["lease"], payload=payload, limit=limit)
        return json.loads(reply["reply"])

    def instance(self, grant):
        return next(i for i in client.call("status")["instances"] if i["id"] == grant["instance"])


class GenerationTest(Fleet):
    def test_publishing_copies_the_build_out_of_the_project(self):
        generation = self.publish(self.project_a)
        manifest = json.loads((self.root / "generations" / generation / "manifest.json").read_text())
        directory = Path(manifest["classpath"][0])
        self.assertEqual((directory / "App.class").read_text(), "classes")
        config = (directory / generations.TEST_CONFIG).read_text()
        self.assertIn(str(self.root), config)
        self.assertNotIn("=build/", config)
        self.assertEqual(len(manifest["classpath"]), 2, "a missing classpath entry is dropped, not kept dangling")
        self.assertEqual(manifest["jvm_args"], ["-ea"], "per-instance settings are the supervisor's")

    def test_generation_is_read_only_and_identical_builds_reuse_it(self):
        first = self.publish(self.project_a)
        self.assertEqual(self.publish(self.project_a), first)
        root = self.root / "generations" / first
        self.assertFalse(stat.S_IMODE(root.stat().st_mode) & 0o222)
        manifest = json.loads((root / "manifest.json").read_text())
        with self.assertRaises(PermissionError):
            (Path(manifest["classpath"][0]) / "App.class").write_text("changed")

    def test_any_change_including_resources_is_a_new_generation(self):
        first = self.publish(self.project_a)
        (self.project_a / "build/AndroidManifest.xml").write_text("<manifest>changed</manifest>")
        self.assertNotEqual(self.publish(self.project_a), first)

    def test_dependencies_are_stored_once_across_projects(self):
        self.publish(self.project_a)
        self.publish(self.project("b", "other"))
        self.assertEqual(len(list((self.root / "blobs").iterdir())), 1)

    def test_a_failed_snapshot_publishes_nothing(self):
        (self.project_a / "build/AndroidManifest.xml").unlink()
        with self.assertRaises(NoemuError) as error:
            self.publish(self.project_a)
        self.assertIn("android_merged_manifest", str(error.exception))
        self.assertEqual(list((self.root / "generations").iterdir()), [])

    def test_a_project_path_in_jvm_arguments_is_refused(self):
        spec = json.loads((self.project_a / "launch.json").read_text())
        spec["jvmArgs"].append(f"-Dcustom={self.project_a}/x")
        (self.project_a / "launch.json").write_text(json.dumps(spec))
        with self.assertRaises(NoemuError) as error:
            self.publish(self.project_a)
        self.assertIn("-Dcustom", str(error.exception))

    def test_running_instance_keeps_its_generation_across_later_builds(self):
        first = self.publish(self.project_a)
        grant = self.acquire()
        self.assertEqual(self.act(grant)["identity"]["build"], first)
        (self.project_a / "build/classes/App.class").write_text("new code")
        second = self.publish(self.project_a)
        self.assertNotEqual(first, second)
        self.assertEqual(self.act(grant)["identity"]["build"], first)
        later = self.acquire()
        self.assertEqual(later["generation"], second)
        self.assertEqual(self.act(later)["identity"]["build"], second)
        (self.project_a / "launch.json").write_text("not a launch record")
        with self.assertRaises(NoemuError):
            self.publish(self.project_a)
        self.assertEqual(self.act(grant)["identity"]["build"], first, "a failed build changes nothing")

    def test_explicit_generation_is_honoured_and_unknown_is_refused(self):
        first = self.publish(self.project_a)
        (self.project_a / "build/classes/App.class").write_text("new code")
        self.publish(self.project_a)
        self.assertEqual(self.acquire(generation=first)["generation"], first)
        with self.assertRaises(NoemuError) as error:
            self.acquire(generation="0000000000000000")
        self.assertEqual(error.exception.code, "no_generation")

    def test_worktrees_have_their_own_current_generation(self):
        a = self.publish(self.project_a)
        other = self.project("b", "branch code")
        b = self.publish(other)
        self.assertNotEqual(a, b)
        self.assertEqual(self.acquire(self.project_a)["generation"], a)
        self.assertEqual(self.acquire(other)["generation"], b)

    def test_acquiring_without_a_build_says_what_to_do(self):
        with self.assertRaises(NoemuError) as error:
            self.acquire()
        self.assertEqual(error.exception.code, "no_generation")
        self.assertIn("noemu build", str(error.exception))


class LeaseTest(Fleet):
    def setUp(self):
        super().setUp()
        self.publish(self.project_a)

    def test_concurrent_agents_never_share_an_instance(self):
        grants = []
        def agent():
            grants.append(self.acquire(wait=30))
            time.sleep(0.05)
        threads = [threading.Thread(target=agent) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len({g["instance"] for g in grants}), 2)
        self.assertEqual(len({g["lease"] for g in grants}), 2)

    def test_an_exclusive_instance_refuses_everyone_without_its_lease(self):
        grant = self.acquire()
        payload = Command("screen").encode("x", 5).decode()
        with self.assertRaises(NoemuError) as error:
            client.call("act", instance=grant["instance"], payload=payload, limit=5)
        self.assertEqual(error.exception.code, "lease_required")
        for op in ("release", "sleep", "wake"):
            with self.subTest(op=op), self.assertRaises(NoemuError) as error:
                client.call(op, instance=grant["instance"])
            self.assertEqual(error.exception.code, "lease_required")
        with self.assertRaises(NoemuError) as error:
            client.call("act", lease=grant["instance"] + ".forged", payload=payload, limit=5)
        self.assertEqual(error.exception.code, "lease_invalid")

    def test_release_confirms_process_exit_and_a_replacement_starts_clean(self):
        grant = self.acquire()
        self.act(grant, "type", "field", "agent one's data")
        self.assertEqual(self.act(grant)["text"], "agent one's data")
        pid = int((self.root / "instances" / grant["instance"] / "pid").read_text())
        client.call("release", lease=grant["lease"])
        self.assertFalse(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
        self.assertFalse((self.root / "instances" / grant["instance"]).exists())
        with self.assertRaises(NoemuError) as error:
            self.act(grant)
        self.assertEqual(error.exception.code, "lease_invalid")
        replacement = self.acquire()
        self.assertNotEqual(replacement["instance"], grant["instance"])
        self.assertEqual(self.act(replacement)["text"], "clean")

    def test_instances_do_not_see_each_other_s_data_or_directories(self):
        first, second = self.acquire(), self.acquire()
        self.act(first, "type", "field", "first's secret")
        self.assertEqual(self.act(second)["text"], "clean")
        self.assertNotEqual(self.act(first, "leaks")["text"].split()[0], self.act(second, "leaks")["text"].split()[0])

    def test_expired_lease_ends_the_instance(self):
        grant = self.acquire(ttl=1)
        wait_until(lambda: not client.call("status")["instances"], message="expiry")
        with self.assertRaises(NoemuError) as error:
            self.act(grant)
        self.assertEqual(error.exception.code, "lease_invalid")

    def test_actions_on_one_instance_are_serialized_while_instances_run_concurrently(self):
        first, second = self.acquire(), self.acquire()
        started = time.monotonic()
        threads = [threading.Thread(target=self.act, args=(first, "wait", "200")) for _ in range(3)]
        threads.append(threading.Thread(target=self.act, args=(second, "wait", "500")))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = time.monotonic() - started
        self.assertEqual(self.act(first, "logs")["text"], "1", "two requests overlapped on one instance")
        self.assertGreaterEqual(elapsed, 0.6)
        self.assertLess(elapsed, 1.05, "instances waited for each other")

    def test_an_unresponsive_app_is_terminated_and_reported_inconclusive(self):
        grant = self.acquire()
        with self.assertRaises(NoemuError) as error:
            self.act(grant, "wait", "120000", limit=0.3)
        self.assertEqual(error.exception.code, "deadline_exceeded")
        self.assertIn("terminated", str(error.exception))
        self.assertEqual(self.instance(grant)["state"], "failed")

    def test_a_crashed_instance_is_reported_and_its_slot_returns(self):
        grant = self.acquire()
        with self.assertRaises(NoemuError):
            self.act(grant, "back")
            self.act(grant, "back")
        wait_until(lambda: self.instance(grant)["state"] == "failed", message="crash detection")
        self.assertIn("exited", self.instance(grant)["error"])
        self.assertTrue((self.root / "instances" / grant["instance"] / "log.txt").exists(), "failure log retained")
        self.assertEqual(client.call("status")["active"], 0)

    def test_sleep_keeps_state_and_refuses_actions_until_woken(self):
        grant = self.acquire()
        self.act(grant, "type", "field", "kept")
        client.call("sleep", lease=grant["lease"])
        pid = int((self.root / "instances" / grant["instance"] / "pid").read_text())
        self.assertEqual(psutil.Process(pid).status(), psutil.STATUS_STOPPED)
        with self.assertRaises(NoemuError) as error:
            self.act(grant)
        self.assertEqual(error.exception.code, "instance_unavailable")
        client.call("wake", lease=grant["lease"])
        self.assertEqual(self.act(grant)["text"], "kept")

    def test_shared_spawn_instances_work_by_id(self):
        grant = self.acquire(shared=True)
        with mock.patch.object(cli, "INSTANCE", grant["instance"]), mock.patch.object(cli, "LEASE", None):
            self.assertEqual(cli.request(Command("screen")).text(), "clean")
        client.call("release", instance=grant["instance"])

    def test_cli_sends_session_commands_through_the_chosen_lease(self):
        grant = self.acquire()
        with mock.patch.object(cli, "INSTANCE", None), mock.patch.object(cli, "LEASE", grant["lease"]):
            result = cli.request(Command("screen"))
        self.assertEqual(result.data["identity"]["instance"], grant["instance"])

    def test_startup_failure_is_reported_and_keeps_the_log(self):
        os.environ["NOEMU_JVM_ARGS"] = "-Dnoemu.badstart=1"
        self.addCleanup(os.environ.pop, "NOEMU_JVM_ARGS")
        self.java.write_text("#!/bin/sh\necho no such runtime\nexit 3\n")
        client.call("hello")
        with self.assertRaises(NoemuError) as error:
            self.acquire()
        self.assertEqual(error.exception.code, "startup_failed")
        failed = client.call("status")["instances"][0]
        self.assertEqual(failed["state"], "failed")
        self.assertIn("no such runtime", (self.root / "instances" / failed["id"] / "log.txt").read_text())


class LimitTest(Fleet):
    def setUp(self):
        super().setUp()
        self.publish(self.project_a)

    def test_active_limit_queues_in_order_and_times_out_truthfully(self):
        first, second = self.acquire(), self.acquire()
        with self.assertRaises(NoemuError) as error:
            self.acquire(wait=0.2)
        self.assertEqual(error.exception.code, "capacity_timeout")
        self.assertIn("2/2 active", str(error.exception))
        order = []
        def agent(name, delay):
            time.sleep(delay)
            order.append((name, self.acquire(wait=20)))
        queued = [threading.Thread(target=agent, args=("early", 0)), threading.Thread(target=agent, args=("late", 0.2))]
        for thread in queued:
            thread.start()
        wait_until(lambda: client.call("status")["queued"] == 2, message="queueing")
        client.call("release", lease=first["lease"])
        wait_until(lambda: order, message="first queued grant")
        client.call("release", lease=second["lease"])
        for thread in queued:
            thread.join()
        self.assertEqual([name for name, _ in order], ["early", "late"])

    def test_a_departed_client_leaves_the_queue(self):
        self.acquire(), self.acquire()
        program = (f"from noemu import client; client.call('acquire', 60, project={str(self.project_a)!r}, wait=60)")
        waiting = subprocess.Popen([sys.executable, "-c", program], env=os.environ)
        self.addCleanup(waiting.wait)
        wait_until(lambda: client.call("status")["queued"] == 1, message="queueing")
        waiting.kill()
        wait_until(lambda: client.call("status")["queued"] == 0, message="queue departure")

    def test_sleeping_instances_free_active_slots_but_still_count_as_jvms(self):
        first, second = self.acquire(), self.acquire()
        client.call("sleep", lease=first["lease"])
        third = self.acquire()
        client.call("sleep", lease=second["lease"])
        fourth = self.acquire()
        self.assertEqual(client.call("status")["active"], 2)
        client.call("sleep", lease=third["lease"])
        with self.assertRaises(NoemuError) as error:
            self.acquire(wait=0.3)
        self.assertIn("4/4 JVMs", str(error.exception))
        with self.assertRaises(NoemuError) as error:
            client.call("sleep", lease=first["lease"])
        self.assertEqual(error.exception.code, "invalid_state")
        client.call("release", lease=fourth["lease"])

    def test_wake_waits_for_an_active_slot(self):
        first, second = self.acquire(), self.acquire()
        client.call("sleep", lease=first["lease"])
        third = self.acquire()
        with self.assertRaises(NoemuError) as error:
            client.call("wake", lease=first["lease"], wait=0.2)
        self.assertEqual(error.exception.code, "capacity_timeout")
        client.call("release", lease=third["lease"])
        client.call("wake", lease=first["lease"], wait=5)
        self.assertEqual(self.act(first)["text"], "clean")
        self.assertEqual(second["instance"] in [i["id"] for i in client.call("status")["instances"]], True)

    def test_concurrent_load_never_exceeds_the_limits(self):
        peak = {"active": 0, "jvms": 0}
        done = threading.Event()
        def watch():
            while not done.is_set():
                status = client.call("status")
                live = [i for i in status["instances"] if i["state"] != "failed"]
                peak["active"] = max(peak["active"], status["active"])
                peak["jvms"] = max(peak["jvms"], len(live))
                time.sleep(0.005)
        watcher = threading.Thread(target=watch)
        watcher.start()
        failures = []
        def agent():
            try:
                for _ in range(3):
                    grant = self.acquire(wait=30)
                    self.act(grant)
                    client.call("release", lease=grant["lease"])
            except Exception as error:
                failures.append(error)
        agents = [threading.Thread(target=agent) for _ in range(6)]
        for thread in agents:
            thread.start()
        for thread in agents:
            thread.join()
        done.set()
        watcher.join()
        self.assertEqual(failures, [])
        self.assertLessEqual(peak["active"], 2)
        self.assertLessEqual(peak["jvms"], 4)

    def test_memory_budget_blocks_admission_and_reports_why(self):
        client.call("shutdown")
        wait_until(lambda: not (self.root / "supervisor.sock").exists(), message="shutdown")
        with mock.patch.dict(os.environ, {"NOEMU_MEMORY_MIB": "65", "NOEMU_RESERVE_MIB": "60"}):
            self.acquire()
            status = client.call("status")
            self.assertEqual(status["memory"]["budget_mib"], 65)
            with self.assertRaises(NoemuError) as error:
                self.acquire(wait=0.3)
            self.assertIn("MiB charged", str(error.exception))


class MemoryRuleTest(unittest.TestCase):
    def test_budget_is_the_smaller_of_4_gib_and_40_percent(self):
        config = supervisor.Config()
        for available, expected in ((32 * 1024, 4096), (5 * 1024, 2048)):
            memory = mock.Mock(available=available * supervisor.MIB)
            with mock.patch.object(supervisor.psutil, "virtual_memory", return_value=memory), \
                    mock.patch.object(supervisor, "cgroup_limit", return_value=None):
                self.assertEqual(config.memory_budget(0) // supervisor.MIB, expected)

    def test_budget_counts_what_the_fleet_itself_holds_and_a_container_limit(self):
        config = supervisor.Config()
        memory = mock.Mock(available=1024 * supervisor.MIB)
        with mock.patch.object(supervisor.psutil, "virtual_memory", return_value=memory), \
                mock.patch.object(supervisor, "cgroup_limit", return_value=None):
            self.assertEqual(config.memory_budget(1024 * supervisor.MIB) // supervisor.MIB, 819)
        memory = mock.Mock(available=32 * 1024 * supervisor.MIB)
        with mock.patch.object(supervisor.psutil, "virtual_memory", return_value=memory), \
                mock.patch.object(supervisor, "cgroup_limit", return_value=1024 * supervisor.MIB):
            self.assertEqual(config.memory_budget(0) // supervisor.MIB, 409)


class WarmTest(Fleet):
    environment = {"NOEMU_WARM": "1"}

    def warm(self):
        return [i for i in client.call("status")["instances"] if i["state"] == "warm"]

    def test_warm_instance_serves_the_next_lease_then_refills(self):
        generation = self.publish(self.project_a)
        client.call("pool", project=str(self.project_a), size=1)
        waiting = wait_until(self.warm, message="warm instance")[0]
        pid = int((self.root / "instances" / waiting["id"] / "pid").read_text())
        self.assertEqual(psutil.Process(pid).status(), psutil.STATUS_STOPPED, "a warm JVM sleeps")
        grant = self.acquire()
        self.assertEqual(grant["instance"], waiting["id"])
        self.assertEqual(self.act(grant)["identity"]["build"], generation)
        refilled = wait_until(self.warm, message="refill")[0]
        self.assertNotEqual(refilled["id"], waiting["id"])

    def test_shrinking_the_pool_destroys_surplus_warm_instances(self):
        self.publish(self.project_a)
        client.call("pool", project=str(self.project_a), size=2)
        warm = wait_until(lambda: len(self.warm()) == 2 and self.warm(), message="two warm instances")
        pids = [int((self.root / "instances" / w["id"] / "pid").read_text()) for w in warm]
        client.call("pool", project=str(self.project_a), size=0)
        self.assertEqual(self.warm(), [])
        for pid in pids:
            self.assertFalse(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)

    def test_shutdown_ends_unowned_warm_instances_and_keeps_leases(self):
        self.publish(self.project_a)
        grant = self.acquire()
        warm = wait_until(self.warm, message="warm instance")[0]
        pid = int((self.root / "instances" / warm["id"] / "pid").read_text())
        client.call("shutdown")
        wait_until(lambda: not (self.root / "supervisor.sock").exists(), message="shutdown")
        self.assertFalse(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE,
                         "a warm JVM nobody owns outlived its supervisor")
        self.assertEqual(self.act(grant)["text"], "clean", "a leased instance is adopted by the next supervisor")

    def stubs(self):
        return [p for p in psutil.process_iter(["cmdline"])
                if any(str(self.root) in part for part in (p.info["cmdline"] or [])) and "stub_driver" in " ".join(p.info["cmdline"])]

    def test_shutdown_ends_instances_that_are_still_starting(self):
        os.environ["STUB_START_SECONDS"] = "2"
        self.addCleanup(os.environ.pop, "STUB_START_SECONDS")
        self.publish(self.project_a)
        with self.assertRaises(NoemuError):
            client.call("pool", project=str(self.project_a), size=1, wait=0.2)
        wait_until(self.stubs, message="a starting instance")
        client.call("shutdown")
        wait_until(lambda: not self.stubs(), timeout=5, message="the starting JVM to end with its supervisor")

    def test_a_refill_finishing_after_the_pool_shrank_is_not_kept(self):
        os.environ["STUB_START_SECONDS"] = "1"
        self.addCleanup(os.environ.pop, "STUB_START_SECONDS")
        self.publish(self.project_a)
        with self.assertRaises(NoemuError):
            client.call("pool", project=str(self.project_a), size=1, wait=0.2)
        client.call("pool", project=str(self.project_a), size=0, wait=5)
        time.sleep(1.5)
        self.assertEqual([i for i in client.call("status")["instances"] if i["state"] in supervisor.LIVE], [])

    def test_a_new_build_retires_warm_instances_of_the_old_one(self):
        old = self.publish(self.project_a)
        client.call("pool", project=str(self.project_a), size=1)
        wait_until(self.warm, message="warm instance")
        (self.project_a / "build/classes/App.class").write_text("new code")
        new = self.publish(self.project_a)
        self.assertNotEqual(old, new)
        self.assertNotIn(old, [i["generation"] for i in client.call("status")["instances"]])
        self.assertEqual(self.acquire()["generation"], new)

    def test_warm_instances_give_way_to_requested_work_at_the_jvm_limit(self):
        self.publish(self.project_a)
        other = self.project("b", "other")
        self.publish(other)
        client.call("pool", project=str(self.project_a), size=4)
        self.assertEqual(len(self.warm()), 4)
        self.assertEqual(self.acquire(other)["instance"] not in [w["id"] for w in self.warm()], True)
        self.assertLessEqual(len(client.call("status")["instances"]), 4)


class RecoveryTest(Fleet):
    def test_a_new_supervisor_adopts_verified_instances_and_keeps_leases(self):
        self.publish(self.project_a)
        grant = self.acquire()
        self.act(grant, "type", "field", "survives")
        os.kill(client.call("status")["pid"], signal.SIGKILL)
        self.assertEqual(self.act(grant)["text"], "survives")
        self.assertEqual(self.instance(grant)["state"], "leased")
        client.call("release", lease=grant["lease"])
        self.assertEqual(client.call("status")["instances"], [])

    def test_an_instance_that_died_with_the_supervisor_is_written_off(self):
        self.publish(self.project_a)
        grant = self.acquire()
        pid = int((self.root / "instances" / grant["instance"] / "pid").read_text())
        os.kill(client.call("status")["pid"], signal.SIGKILL)
        os.kill(pid, signal.SIGKILL)
        wait_until(lambda: self.gone(pid), message="process exit")
        self.assertEqual(self.instance(grant)["state"], "failed")
        with self.assertRaises(NoemuError) as error:
            self.act(grant)
        self.assertEqual(error.exception.code, "lease_invalid")

    def gone(self, pid):
        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True

    def test_a_reused_pid_is_never_signalled(self):
        self.publish(self.project_a)
        grant = self.acquire()
        directory = self.root / "instances" / grant["instance"]
        pid = int((directory / "pid").read_text())
        os.kill(client.call("status")["pid"], signal.SIGKILL)
        bystander = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(bystander.wait)
        self.addCleanup(bystander.kill)
        os.kill(pid, signal.SIGKILL)
        wait_until(lambda: self.gone(pid), message="process exit")
        identity = json.loads((directory / "process.json").read_text())
        identity["pid"] = bystander.pid
        (directory / "process.json").write_text(json.dumps(identity))
        (directory / "pid").write_text(str(bystander.pid))
        self.assertEqual(self.instance(grant)["state"], "failed")
        self.assertIsNone(bystander.poll())

    def test_only_one_supervisor_serves(self):
        client.call("hello")
        pid = client.call("status")["pid"]
        self.assertEqual(subprocess.run([sys.executable, "-m", "noemu", "_supervisor"], env=os.environ, timeout=10).returncode, 0)
        self.assertEqual(client.call("status")["pid"], pid)

    def test_an_older_supervisor_is_replaced_and_its_instances_adopted(self):
        self.publish(self.project_a)
        grant = self.acquire()
        pid = client.call("status")["pid"]
        with mock.patch.object(supervisor, "code_identity", return_value="newer code"), \
                mock.patch.object(client, "start", side_effect=lambda root: None):
            with self.assertRaises(NoemuError):
                client.call("hello")
        wait_until(lambda: not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE, message="replacement")
        self.assertEqual(self.act(grant)["text"], "clean")


class RetentionTest(Fleet):
    def builds(self, count):
        ids = []
        for number in range(count):
            (self.project_a / "build/classes/App.class").write_text(f"build {number}")
            ids.append(self.publish(self.project_a))
        return ids

    def stored(self):
        return {p.name for p in (self.root / "generations").iterdir()}

    def test_retention_keeps_recent_and_in_use_generations(self):
        first = self.builds(1)[0]
        grant = self.acquire()
        ids = self.builds(5)
        self.assertEqual(self.stored(), {first, *ids[-3:]})
        client.call("release", lease=grant["lease"])
        client.call("gc")
        self.assertEqual(self.stored(), set(ids[-3:]))

    def test_gc_removes_abandoned_partial_builds_and_unlinked_blobs_only(self):
        self.builds(1)
        partial = self.root / "generations" / ".tmp-abandoned"
        partial.mkdir()
        unlinked = self.root / "blobs" / "0"
        unlinked.write_text("orphan")
        kept = {p.name for p in (self.root / "blobs").iterdir()} - {"0"}
        client.call("gc")
        self.assertFalse(partial.exists())
        self.assertEqual({p.name for p in (self.root / "blobs").iterdir()}, kept)
        self.assertTrue(kept)

    def test_failure_logs_are_bounded(self):
        self.builds(1)
        self.java.write_text("#!/bin/sh\nexit 3\n")
        for _ in range(11):
            with self.assertRaises(NoemuError):
                self.acquire()
        client.call("gc")
        self.assertEqual(len(list((self.root / "instances").iterdir())), 8)


class HomeTest(unittest.TestCase):
    def test_a_directory_others_can_write_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"NOEMU_HOME": tmp}):
            os.chmod(tmp, 0o755)
            with self.assertRaises(generations.GenerationError):
                generations.home()
            os.chmod(tmp, 0o700)
            self.assertEqual(generations.home(), Path(tmp))

    def test_a_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir(mode=0o700)
            link = Path(tmp) / "link"
            link.symlink_to(target)
            with mock.patch.dict(os.environ, {"NOEMU_HOME": str(link)}), self.assertRaises(generations.GenerationError):
                generations.home()


class CommandLineTest(Fleet):
    def run_cli(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, "-m", "noemu", *args], cwd=cwd or self.project_a, capture_output=True, text=True,
                              timeout=60, env={**os.environ, **(env or {})})

    def test_lease_workflow_with_json_output(self):
        self.publish(self.project_a)
        result = self.run_cli("acquire", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        grant = json.loads(result.stdout)
        lease = {"NOEMU_LEASE": grant["lease"]}
        self.assertEqual(self.run_cli("screen", env=lease).stdout.strip(), "clean")
        self.assertEqual(self.run_cli("sleep", env=lease).stdout.split()[1], "asleep")
        refused = self.run_cli("screen", env=lease)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("instance_unavailable", refused.stderr)
        self.assertEqual(self.run_cli("wake", env=lease).stdout.split()[1], "awake")
        report = json.loads(self.run_cli("status", "--json").stdout)
        self.assertEqual(report["instances"][0]["id"], grant["instance"])
        self.assertTrue(report["instances"][0]["exclusive"])
        self.assertEqual(self.run_cli("release", env=lease).returncode, 0)
        self.assertEqual(json.loads(self.run_cli("status", "--json").stdout)["instances"], [])
        self.assertIn("generations", json.loads(self.run_cli("gc", "--json").stdout))

    def test_legacy_spawn_id_and_destroy_are_preserved(self):
        self.publish(self.project_a)
        spawned = self.run_cli("spawn", "--no-refill")
        self.assertEqual(spawned.returncode, 0, spawned.stderr)
        identifier = spawned.stdout.split()[0]
        self.assertEqual(self.run_cli("--id", identifier, "screen").stdout.strip(), "clean")
        self.assertEqual(self.run_cli("screen", env={"NOEMU_ID": identifier}).stdout.strip(), "clean")
        listed = self.run_cli("list").stdout
        self.assertIn(f"{identifier}\tleased", listed)
        self.assertIn("MiB", listed)
        self.assertEqual(self.run_cli("destroy", "all").stdout.strip(), f"{identifier} destroyed")
        self.assertEqual(self.run_cli("list").stdout, "")

    def test_destroy_all_stays_in_its_project_and_spares_exclusive_leases(self):
        self.publish(self.project_a)
        other = self.project("b", "other")
        self.publish(other)
        spawned = self.run_cli("spawn", "--no-refill", cwd=other).stdout.split()[0]
        exclusive = self.acquire()
        self.assertEqual(self.run_cli("destroy", "all").stdout, "")
        self.assertEqual(sorted(i["id"] for i in client.call("status")["instances"]), sorted([spawned, exclusive["instance"]]))

    def test_unknown_arguments_fail(self):
        for arguments in (("acquire", "--typo", "1"), ("status", "extra"), ("gc", "extra"), ("acquire", "--wait", "soon")):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid_argument", result.stderr)

    def test_memory_unknown_is_not_zero(self):
        self.assertEqual(cli.memory({"resident_mib": None}), "memory unknown")


class McpToolTest(Fleet):
    def test_tools_use_leases(self):
        self.publish(self.project_a)
        cwd = os.getcwd()
        os.chdir(self.project_a)
        self.addCleanup(os.chdir, cwd)
        grant = json.loads(cli.run_tool("acquire", {}).content[0]["text"])
        arguments = {"id": grant["instance"], "lease": grant["lease"]}
        self.assertEqual(cli.run_tool("screen", arguments).result.text(), "clean")
        with self.assertRaises(NoemuError) as error:
            cli.run_tool("screen", {"id": grant["instance"]})
        self.assertEqual(error.exception.code, "lease_required")
        self.assertEqual(json.loads(cli.run_tool("status", {}).content[0]["text"])["instances"][0]["id"], grant["instance"])
        cli.run_tool("release", arguments)
        self.assertEqual(json.loads(cli.run_tool("status", {}).content[0]["text"])["instances"], [])


if __name__ == "__main__":
    unittest.main()
