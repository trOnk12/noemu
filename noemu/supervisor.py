"""The per-user fleet supervisor: one daemon shared by every project and worktree of an OS user.

It alone starts, signals and destroys instances, so limits and exclusive leases hold across concurrent agents. SQLite
holds what must survive a supervisor crash; queues and locks live in memory and die with the clients waiting on them.
Clients speak one JSON object per line over a private Unix socket. See docs/SUPERVISOR.md.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import select
import signal
import socket
import socketserver
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil

from noemu import generations, processes, transport
from noemu.protocol import NoemuError

PROTOCOL = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024
MIB = 1024 * 1024
# States holding a JVM. A sleeping JVM keeps its memory, so it counts against every limit but `active`.
LIVE = ("starting", "warm", "leased", "asleep", "stopping")
SCHEMA = """
create table if not exists generations(id text primary key, project text not null, module text not null,
                                       published real not null, manifest text not null);
create table if not exists projects(project text primary key, generation text not null references generations(id));
create table if not exists instances(id integer primary key autoincrement, generation text not null references generations(id),
                                     state text not null, port integer, lease text, shared integer not null default 0,
                                     owner text, expires real, created real not null, ended real, error text);
"""


def code_identity() -> str:
    """Changes when this code does; a client finding an older supervisor replaces it."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def cgroup_limit() -> int | None:
    try:
        value = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


@dataclass
class Config:
    max_active: int = 2
    max_jvms: int = 4
    max_cold: int = 1
    warm: int = 1
    heap: str = "1g"
    reserve_mib: int = 768  # Charged for a JVM until it has a measured resident size.
    memory_mib: int | None = None  # None: the smaller of 4 GiB and 40% of available memory.
    lease_seconds: float = 1800
    start_seconds: float = 300
    keep_generations: int = 3
    keep_failed: int = 8
    idle_exit_seconds: float = 1800

    @classmethod
    def from_environment(cls) -> "Config":
        config = cls()
        for name, kind in (("max_active", int), ("max_jvms", int), ("max_cold", int), ("warm", int), ("reserve_mib", int),
                           ("memory_mib", int), ("lease_seconds", float), ("start_seconds", float),
                           ("keep_generations", int), ("keep_failed", int), ("idle_exit_seconds", float)):
            value = os.environ.get("NOEMU_" + name.upper())
            if value:
                setattr(config, name, kind(value))
        config.heap = os.environ.get("NOEMU_XMX", config.heap)
        return config

    def memory_budget(self, held: int) -> int:
        if self.memory_mib is not None:
            return self.memory_mib * MIB
        available = psutil.virtual_memory().available + held
        limit = cgroup_limit()
        return min(4096 * MIB, int(0.4 * (min(available, limit) if limit else available)))


@dataclass
class Waiter:
    generation: str | None
    owner: str
    shared: bool
    ttl: float
    wake: int | None = None  # An asleep instance waiting for an active slot, instead of a new lease.
    instance: int | None = None
    lease: str | None = None
    error: NoemuError | None = None
    done: threading.Event = field(default_factory=threading.Event)


class Supervisor:
    def __init__(self, root: Path, config: Config):
        self.root, self.config = root, config
        self.instances_dir = root / "instances"
        self.instances_dir.mkdir(exist_ok=True)
        self.mutex = threading.RLock()
        self.storage = threading.Lock()  # Publishing and collecting never overlap. Taken before the mutex.
        self.queue: list[Waiter] = []
        self.children: dict[int, subprocess.Popen] = {}
        self.action_locks: dict[int, threading.Lock] = {}
        self.starting: dict[int, Waiter | None] = {}
        self.last_generation: str | None = None
        self.scheduling = self.reschedule = False
        self.last_busy = time.monotonic()
        self.stopping = threading.Event()
        self.database = sqlite3.connect(root / "fleet.sqlite3", check_same_thread=False, isolation_level=None)
        self.database.row_factory = sqlite3.Row
        self.database.executescript("pragma journal_mode=wal; pragma foreign_keys=on;" + SCHEMA)
        self.recover()

    # Metadata ---------------------------------------------------------------------------------------------------

    def rows(self, where: str = "1", *values) -> list[sqlite3.Row]:
        return self.database.execute(f"select * from instances where {where} order by id", values).fetchall()

    def row(self, instance: int) -> sqlite3.Row:
        found = self.rows("id=?", instance)
        if not found:
            raise NoemuError("not_running", f"no instance {instance}")
        return found[0]

    def set_state(self, instance: int, state: str, **fields) -> None:
        names = "".join(f", {name}=?" for name in fields)
        self.database.execute(f"update instances set state=?{names} where id=?", (state, *fields.values(), instance))

    def directory(self, instance: int) -> Path:
        return self.instances_dir / str(instance)

    def manifest(self, generation: str) -> dict:
        row = self.database.execute("select manifest from generations where id=?", (generation,)).fetchone()
        if not row:
            raise NoemuError("no_generation", f"unknown generation {generation}")
        return json.loads(row["manifest"])

    def count(self, *states: str) -> int:
        marks = ",".join("?" * len(states))
        return self.database.execute(f"select count(*) from instances where state in ({marks})", states).fetchone()[0]

    def resident(self, instance: int) -> int | None:
        return processes.resident_memory(self.directory(instance))

    def held_memory(self) -> tuple[int, int]:
        """Bytes this fleet holds, and the same with each unmeasured JVM charged its reservation."""
        measured = charged = 0
        for row in self.rows(f"state in ({','.join('?' * len(LIVE))})", *LIVE):
            size = self.resident(row["id"])
            measured += size or 0
            # A starting JVM is still growing: charge what it will take rather than what it holds so far.
            charged += self.config.reserve_mib * MIB if size is None or row["state"] == "starting" else size
        return measured, charged

    # Recovery ---------------------------------------------------------------------------------------------------

    def recover(self) -> None:
        """A crashed supervisor's instances are adopted if their identity verifies, and written off if not."""
        for row in self.rows(f"state in ({','.join('?' * len(LIVE))})", *LIVE):
            directory = self.directory(row["id"])
            if row["state"] in ("starting", "stopping"):
                self.remove_instance(row["id"], "failed", f"supervisor restarted while {row['state']}")
            elif not processes.alive(directory):
                self.remove_instance(row["id"], "failed", "process was gone when the supervisor restarted")
            else:
                self.action_locks[row["id"]] = threading.Lock()

    def remove_instance(self, instance: int, final: str, error: str | None = None) -> None:
        """Ends the verified process, if any, and removes what the instance owns. Keeps the log of a failure."""
        directory = self.directory(instance)
        try:
            processes.terminate(directory)
        except processes.ProcessIdentityError:
            pass  # Already gone, or not provably ours: never signal it.
        except TimeoutError as timeout:
            self.set_state(instance, "stopping", error=str(timeout))
            return
        child = self.children.pop(instance, None)
        if child:
            child.wait()
        self.action_locks.pop(instance, None)
        self.set_state(instance, final, ended=time.time(), error=error, port=None, lease=None)
        generations.remove_tree(directory / "tmp")
        if final == "destroyed":
            generations.remove_tree(directory)
        self.schedule()

    # Scheduling -------------------------------------------------------------------------------------------------

    def active(self) -> int:
        return self.count("leased") + sum(1 for w in self.starting.values() if w is not None)

    def make_room(self) -> bool:
        """True when one more JVM fits. Idle warm instances give way to work that has been asked for."""
        while True:
            jvms = self.count(*LIVE)
            measured, charged = self.held_memory()
            budget = self.config.memory_budget(measured)
            if jvms < self.config.max_jvms and charged + self.config.reserve_mib * MIB <= budget:
                return True
            warm = self.rows("state='warm'")
            if not warm:
                return False
            self.remove_instance(warm[0]["id"], "destroyed")

    def lease_out(self, instance: int, waiter: Waiter) -> None:
        waiter.instance, waiter.lease = instance, f"{instance}.{secrets.token_urlsafe(24)}"
        self.set_state(instance, "leased", lease=waiter.lease, shared=int(waiter.shared), owner=waiter.owner,
                       expires=time.time() + waiter.ttl)
        waiter.done.set()

    def fail(self, waiter: Waiter, error: NoemuError) -> None:
        waiter.error = error
        waiter.done.set()

    def schedule(self) -> None:
        """Runs after every change. Evicting a warm instance while scheduling is such a change: it repeats the pass."""
        with self.mutex:
            if self.scheduling:
                self.reschedule = True
                return
            self.scheduling = True
            try:
                self.reschedule = True
                while self.reschedule:
                    self.reschedule = False
                    self.schedule_pass()
            finally:
                self.scheduling = False

    def schedule_pass(self) -> None:
        """Serves the queue strictly in order, then tops up the warm pool."""
        with self.mutex:
            while self.queue and self.active() < self.config.max_active:
                head = self.queue[0]
                if head.wake is not None:
                    self.queue.pop(0)
                    try:
                        processes.send_signal(self.directory(head.wake), signal.SIGCONT)
                        self.set_state(head.wake, "leased")
                        head.done.set()
                    except processes.ProcessIdentityError as error:
                        self.fail(head, NoemuError("instance_unavailable", str(error)))
                    continue
                warm = self.rows("state='warm' and generation=?", head.generation)
                claimed = False
                for row in warm:
                    try:
                        processes.send_signal(self.directory(row["id"]), signal.SIGCONT)
                    except processes.ProcessIdentityError:
                        self.remove_instance(row["id"], "failed", "warm process could not be verified")
                        continue
                    self.queue.pop(0)
                    self.lease_out(row["id"], head)
                    claimed = True
                    break
                if claimed:
                    continue
                if len(self.starting) >= self.config.max_cold or not self.make_room():
                    break
                self.queue.pop(0)
                self.launch(head.generation, head)
            self.refill()

    def refill(self) -> None:
        generation = self.last_generation
        if not generation or self.queue or self.stopping.is_set() or len(self.starting) >= self.config.max_cold:
            return
        waiting = sum(1 for w in self.starting.values() if w is None)
        warm = len(self.rows("state='warm' and generation=?", generation)) + waiting
        if warm < self.config.warm and self.count(*LIVE) < self.config.max_jvms and self.make_room_without_eviction():
            self.launch(generation, None)

    def make_room_without_eviction(self) -> bool:
        measured, charged = self.held_memory()
        return charged + self.config.reserve_mib * MIB <= self.config.memory_budget(measured)

    def launch(self, generation: str, waiter: Waiter | None) -> None:
        try:
            manifest = self.manifest(generation)
        except NoemuError as error:
            if waiter:
                self.fail(waiter, error)
            return
        cursor = self.database.execute("insert into instances(generation, state, created) values(?, 'starting', ?)",
                                       (generation, time.time()))
        instance = cursor.lastrowid
        directory = self.directory(instance)
        (directory / "tmp").mkdir(parents=True)
        (directory / "run").mkdir()  # Working directory: relative paths in the app stay inside the instance.
        (directory / "build").write_text(generation)
        arguments = [*manifest["jvm_args"], f"-Xmx{self.config.heap}", f"-Dnoemu.out={directory}", f"-Dnoemu.instance={instance}",
                     f"-Djava.io.tmpdir={directory / 'tmp'}"]
        arguments += [f"-Dnoemu.{k}={os.environ[v]}" for k, v in (("settle", "NOEMU_SETTLE_MS"), ("shots", "NOEMU_SHOTS"))
                      if os.environ.get(v)]
        arguments += os.environ.get("NOEMU_JVM_ARGS", "-XX:+UseSerialGC -XX:TieredStopAtLevel=1").split()
        try:
            with open(directory / "log.txt", "w") as log:
                child = subprocess.Popen([manifest["java"], *arguments, "-cp", os.pathsep.join(manifest["classpath"]),
                                          "org.junit.runner.JUnitCore", "dev.noemu.NoemuDriver"], cwd=directory / "run",
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.remember(directory, child.pid)
        except OSError as error:
            self.set_state(instance, "failed", ended=time.time(), error=str(error))
            if waiter:
                self.fail(waiter, NoemuError("startup_failed", str(error)))
            return
        self.children[instance] = child
        self.action_locks[instance] = threading.Lock()
        self.starting[instance] = waiter
        threading.Thread(target=self.await_ready, args=(instance, child), daemon=True).start()

    @staticmethod
    def published_port(directory: Path) -> int | None:
        """The driver's port once written completely: the file may exist before its digits do."""
        try:
            port = int((directory / "port").read_text())
        except (OSError, ValueError):
            return None
        return port if 1 <= port <= 65535 else None

    def await_ready(self, instance: int, child: subprocess.Popen) -> None:
        directory = self.directory(instance)
        deadline = time.monotonic() + self.config.start_seconds
        port = None
        while time.monotonic() < deadline and child.poll() is None and port is None:
            if self.stopping.wait(0.02):
                return
            port = self.published_port(directory)
        with self.mutex:
            waiter = self.starting.pop(instance, None)
            ready = child.poll() is None and port is not None
            if ready:
                try:
                    self.set_state(instance, "warm", port=port)
                    if waiter:
                        self.lease_out(instance, waiter)
                    else:
                        processes.send_signal(directory, signal.SIGSTOP)
                except (OSError, processes.ProcessIdentityError) as error:
                    ready = False
                    reason = f"instance failed after starting: {error}"
            else:
                reason = f"instance {instance} did not start; see {directory / 'log.txt'}"
            if not ready:
                self.remove_instance(instance, "failed", reason)
                if waiter:
                    self.fail(waiter, NoemuError("startup_failed", reason))
            self.schedule()

    # Operations -------------------------------------------------------------------------------------------------

    def op_hello(self, args: dict, connection) -> dict:
        return {"protocol": PROTOCOL, "code": code_identity(), "pid": os.getpid()}

    def op_publish(self, args: dict, connection) -> dict:
        # Copying can take a while: only the registration below needs the mutex.
        project = str(Path(args["project"]).resolve())
        with self.storage:
            manifest = generations.publish(self.root, Path(project), args["module"], args["task"], Path(args["spec"]))
            with self.mutex:
                self.database.execute("begin immediate")
                # Identical contents may come from another worktree: the same generation serves both.
                self.database.execute("insert or ignore into generations values(?, ?, ?, ?, ?)",
                                      (manifest["id"], manifest["project"], manifest["module"], time.time(), json.dumps(manifest)))
                previous = self.database.execute("select generation from projects where project=?", (project,)).fetchone()
                self.database.execute("insert or replace into projects values(?, ?)", (project, manifest["id"]))
                self.database.execute("commit")
                if previous and previous["generation"] != manifest["id"]:
                    if self.last_generation == previous["generation"]:
                        self.last_generation = manifest["id"]
                    for row in self.rows("state='warm' and generation=?", previous["generation"]):
                        self.remove_instance(row["id"], "destroyed")  # Never hand the old code to a new lease.
                self.collect()
                self.schedule()
        return {"generation": manifest["id"], "project": project}

    def current_generation(self, project: str) -> str:
        row = self.database.execute("select generation from projects where project=?", (str(Path(project).resolve()),)).fetchone()
        if not row:
            raise NoemuError("no_generation", "no build recorded for this project; `noemu build` first")
        return row["generation"]

    def client_gone(self, connection) -> bool:
        try:
            readable, _, _ = select.select([connection], [], [], 0)
            return bool(readable) and connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def op_acquire(self, args: dict, connection) -> dict:
        with self.mutex:
            generation = args.get("generation") or self.current_generation(args["project"])
            self.manifest(generation)
            if args.get("refill", True):
                self.last_generation = generation
            ttl = float(args.get("ttl") or self.config.lease_seconds)
            if not 1 <= ttl <= 86400:
                raise NoemuError("invalid_argument", "lease seconds must be between 1 and 86400")
            waiter = Waiter(generation, str(args.get("owner", "")), bool(args.get("shared")), ttl)
            self.queue.append(waiter)
            self.schedule()
        deadline = time.monotonic() + float(args.get("wait", 600))
        while not waiter.done.wait(0.05):
            expired = time.monotonic() >= deadline
            if expired or self.client_gone(connection):
                with self.mutex:
                    if waiter.done.is_set():
                        break
                    if waiter in self.queue:
                        self.queue.remove(waiter)
                    else:  # Its JVM is starting; let it join the warm pool.
                        self.starting.update({i: None for i, w in self.starting.items() if w is waiter})
                    self.schedule()
                raise NoemuError("capacity_timeout", "no instance became available in time: " + self.pressure())
        if waiter.error:
            raise waiter.error
        with self.mutex:
            row = self.row(waiter.instance)
            if self.client_gone(connection):
                self.remove_instance(waiter.instance, "destroyed")
                raise NoemuError("connection_lost", "client left before receiving its lease")
            return {"instance": str(row["id"]), "lease": waiter.lease, "generation": row["generation"], "port": row["port"],
                    "expires": row["expires"]}

    def pressure(self) -> str:
        measured, charged = self.held_memory()
        return (f"{self.active()}/{self.config.max_active} active, {self.count(*LIVE)}/{self.config.max_jvms} JVMs, "
                f"{charged // MIB}/{self.config.memory_budget(measured) // MIB} MiB charged, {len(self.queue)} queued")

    def authorized(self, args: dict) -> sqlite3.Row:
        """The instance a request may act on. Exclusive leases need their secret; `spawn` instances need only the ID."""
        lease = args.get("lease")
        if lease:
            found = self.rows("lease=?", lease)
            if not found:
                raise NoemuError("lease_invalid", "this lease is not held; it was released, expired, or its instance failed")
            return found[0]
        identifier = str(args.get("instance", ""))
        if not identifier.isdigit():
            raise NoemuError("invalid_argument", "a lease or instance id is required")
        row = self.row(int(identifier))
        if row["state"] not in LIVE:
            raise NoemuError("not_running", f"instance {identifier} is {row['state']}")
        if not row["shared"]:
            raise NoemuError("lease_required", f"instance {identifier} is exclusively leased; use its NOEMU_LEASE")
        return row

    def op_act(self, args: dict, connection) -> dict:
        timeout = float(args["limit"])
        with self.mutex:
            row = self.authorized(args)
            instance = row["id"]
            if row["state"] != "leased":
                raise NoemuError("instance_unavailable", f"instance {instance} is {row['state']}; `noemu wake` it first")
            lock = self.action_locks[instance]
        # One action at a time per instance; instances do not wait for each other.
        if not lock.acquire(timeout=timeout):
            raise NoemuError("deadline_exceeded", f"instance {instance} stayed busy for {timeout:g} s; the command was not sent")
        try:
            with self.mutex:
                row = self.authorized(args)
                if row["state"] != "leased":
                    raise NoemuError("instance_unavailable", f"instance {instance} is {row['state']}")
                port = row["port"]
                self.database.execute("update instances set expires=? where id=?",
                                      (time.time() + self.config.lease_seconds, instance))
            try:
                reply = transport.exchange(port, args["payload"].encode("utf-8"), timeout)
            except NoemuError as error:
                if error.code == "deadline_exceeded":
                    # An uninterruptible app cannot be reasoned about any further; end it so its lease is honest.
                    with self.mutex:
                        self.remove_instance(instance, "failed", error.message)
                    raise NoemuError("deadline_exceeded", f"{error.message}; instance {instance} was terminated") from None
                raise
            return {"reply": reply.decode("utf-8"), "generation": row["generation"]}
        finally:
            lock.release()

    def op_release(self, args: dict, connection) -> dict:
        with self.mutex:
            row = self.authorized(args)
            # An action in progress dies with the process; the caller gave up the instance.
            self.remove_instance(row["id"], "destroyed")
        return {"instance": str(row["id"]), "state": "destroyed"}

    def op_sleep(self, args: dict, connection) -> dict:
        with self.mutex:
            row = self.authorized(args)
            if row["state"] != "leased":
                raise NoemuError("invalid_state", f"instance {row['id']} is {row['state']}")
            lock = self.action_locks[row["id"]]
        if not lock.acquire(timeout=float(args.get("wait", 120))):
            raise NoemuError("deadline_exceeded", f"instance {row['id']} is busy")
        try:
            with self.mutex:
                row = self.authorized(args)
                processes.send_signal(self.directory(row["id"]), signal.SIGSTOP)
                self.set_state(row["id"], "asleep")
                self.schedule()
        finally:
            lock.release()
        return {"instance": str(row["id"]), "state": "asleep"}

    def op_wake(self, args: dict, connection) -> dict:
        with self.mutex:
            row = self.authorized(args)
            if row["state"] == "leased":
                return {"instance": str(row["id"]), "state": "leased"}
            if row["state"] != "asleep":
                raise NoemuError("invalid_state", f"instance {row['id']} is {row['state']}")
            waiter = Waiter(None, row["owner"] or "", bool(row["shared"]), 0, wake=row["id"])
            self.queue.append(waiter)
            self.schedule()
        if not waiter.done.wait(float(args.get("wait", 600))):
            with self.mutex:
                if waiter in self.queue:
                    self.queue.remove(waiter)
                    raise NoemuError("capacity_timeout", "no active slot became available: " + self.pressure())
        if waiter.error:
            raise waiter.error
        with self.mutex:
            self.database.execute("update instances set expires=? where id=?", (time.time() + self.config.lease_seconds, row["id"]))
        return {"instance": str(row["id"]), "state": "leased"}

    def op_pool(self, args: dict, connection) -> dict:
        """Sets the warm pool for a project's current build, and waits until it is ready or cannot grow."""
        size = int(args["size"])
        if not 0 <= size <= self.config.max_jvms:
            raise NoemuError("invalid_argument", f"pool size must be between 0 and {self.config.max_jvms}")
        with self.mutex:
            self.last_generation = self.current_generation(args["project"])
            self.config.warm = size
            self.schedule()
        deadline = time.monotonic() + float(args.get("wait", 600))
        while time.monotonic() < deadline:
            with self.mutex:
                warm = self.rows("state='warm' and generation=?", self.last_generation)
                if len(warm) >= size or not self.starting:
                    return {"warm": [str(r["id"]) for r in warm], "requested": size, "pressure": self.pressure()}
            time.sleep(0.05)
        raise NoemuError("capacity_timeout", "the pool did not fill in time: " + self.pressure())

    def op_status(self, args: dict, connection) -> dict:
        with self.mutex:
            current = {r["project"]: r["generation"] for r in self.database.execute("select * from projects")}
            instances = []
            for row in self.rows(f"state in ({','.join('?' * len(LIVE))}) or state='failed'", *LIVE):
                size = self.resident(row["id"]) if row["state"] in LIVE else None
                instances.append({"id": str(row["id"]), "state": row["state"], "generation": row["generation"],
                                  "project": self.manifest(row["generation"])["project"],
                                  "port": row["port"], "owner": row["owner"], "exclusive": bool(row["lease"]) and not row["shared"],
                                  "expires": row["expires"], "resident_mib": None if size is None else round(size / MIB, 1),
                                  "error": row["error"],
                                  "current": row["generation"] in current.values()})
            measured, charged = self.held_memory()
            return {"pid": os.getpid(), "limits": asdict(self.config), "instances": instances,
                    "memory": {"resident_mib": measured // MIB, "charged_mib": charged // MIB,
                               "budget_mib": self.config.memory_budget(measured) // MIB},
                    "active": self.active(), "queued": len(self.queue), "projects": current}

    def collect(self) -> dict:
        """Bounded retention: recent generations of each project, anything in use, and the latest failure logs."""
        keep = {row["generation"] for row in self.rows(f"state in ({','.join('?' * len(LIVE))})", *LIVE)}
        keep |= {r["generation"] for r in self.database.execute("select generation from projects")}
        for project in self.database.execute("select distinct project from generations").fetchall():
            recent = self.database.execute("select id from generations where project=? order by published desc limit ?",
                                           (project["project"], self.config.keep_generations))
            keep |= {r["id"] for r in recent}
        ended = self.rows("state in ('failed', 'destroyed')")
        keep_logs = {r["id"] for r in sorted((r for r in ended if r["state"] == "failed"), key=lambda r: r["id"])[-self.config.keep_failed:]}
        removed_logs = 0
        for row in ended:
            if row["id"] not in keep_logs:
                self.database.execute("delete from instances where id=?", (row["id"],))
        known = {r["id"] for r in self.rows()}
        for directory in self.instances_dir.iterdir():
            if not directory.name.isdigit() or int(directory.name) not in known:
                generations.remove_tree(directory)
                removed_logs += 1
        referenced = {r["generation"] for r in self.rows()}
        keep |= referenced
        rows = self.database.execute("select id from generations").fetchall()
        for row in rows:
            if row["id"] not in keep:
                self.database.execute("delete from generations where id=?", (row["id"],))
        result = generations.collect(self.root, keep)
        result["instance_directories"] = removed_logs
        return result

    def op_gc(self, args: dict, connection) -> dict:
        with self.storage, self.mutex:
            return self.collect()

    def op_shutdown(self, args: dict, connection) -> dict:
        """Stops the supervisor only; instances keep running and the next supervisor adopts them."""
        self.stopping.set()
        return {"stopped": True}

    # Background ------------------------------------------------------------------------------------------------

    def reap(self) -> None:
        with self.mutex:
            now = time.time()
            for row in self.rows("state in ('leased', 'asleep', 'warm', 'stopping')"):
                directory = self.directory(row["id"])
                child = self.children.get(row["id"])
                gone = (child is not None and child.poll() is not None) or not processes.alive(directory)
                if row["state"] == "stopping":
                    self.remove_instance(row["id"], "destroyed")
                elif gone:
                    self.remove_instance(row["id"], "failed", "the process exited unexpectedly")
                elif row["state"] != "warm" and row["expires"] is not None and row["expires"] < now:
                    self.remove_instance(row["id"], "destroyed")
            if self.queue or self.starting or self.count(*LIVE):
                self.last_busy = time.monotonic()
            elif time.monotonic() - self.last_busy > self.config.idle_exit_seconds:
                self.stopping.set()
            self.schedule()

    def background(self) -> None:
        while not self.stopping.wait(0.5):
            try:
                self.reap()
            except Exception as error:  # A failed pass must not end reaping; it is retried and logged.
                print(f"noemu supervisor: reap failed: {error!r}", file=sys.stderr, flush=True)


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        supervisor: Supervisor = self.server.supervisor
        if hasattr(socket, "SO_PEERCRED"):
            _, uid, _ = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                return
        line = self.rfile.readline(MAX_FRAME_BYTES + 1)
        if not line or len(line) > MAX_FRAME_BYTES:
            return
        try:
            request = json.loads(line)
            operation = getattr(supervisor, "op_" + str(request.get("op")), None)
            if operation is None:
                raise NoemuError("unsupported", f"unknown operation {request.get('op')}")
            reply = {"ok": True, "result": operation(request, self.connection)}
        except NoemuError as error:
            reply = {"ok": False, "code": error.code, "message": error.message}
        except (generations.GenerationError, KeyError, TypeError, ValueError) as error:
            reply = {"ok": False, "code": "invalid_argument", "message": str(error)}
        except sqlite3.Error as error:
            reply = {"ok": False, "code": "invalid_state", "message": f"metadata failure: {error}"}
        except Exception as error:  # A defect here must reach the client as a failure, not as a dropped connection.
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            reply = {"ok": False, "code": "internal_error", "message": f"{error!r}; outcome unknown; see supervisor.log"}
        try:
            self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")
        except OSError:
            pass  # The client left; leases and queues notice on their own.


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 128


def serve(root: Path | None = None) -> int:
    root = root or generations.home()
    lock = open(root / "supervisor.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # Another supervisor holds it: exactly one per user.
    path = root / "supervisor.sock"
    path.unlink(missing_ok=True)
    supervisor = Supervisor(root, Config.from_environment())
    server = Server(str(path), Handler)
    path.chmod(0o600)
    server.supervisor = supervisor
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: supervisor.stopping.set())
    threading.Thread(target=supervisor.background, daemon=True).start()
    supervisor.stopping.wait()
    time.sleep(0.1)  # Lets the shutdown reply leave.
    server.shutdown()
    server.server_close()
    path.unlink(missing_ok=True)
    return 0
