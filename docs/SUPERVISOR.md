# Fleet supervisor

One supervisor process per OS user serves every project and worktree of that user.
It alone starts, signals, and destroys app JVMs, so limits and exclusive leases hold
across concurrent agents. The CLI starts it on demand. A client that finds an older
supervisor replaces it, and the new one adopts the running instances.

## Generations

`noemu build` compiles the app, then publishes a **generation**: a read-only snapshot
of everything the app JVM loads.

- Classpath entries inside the project are copied. Dependency jars are stored once,
  by SHA-256, and hard-linked into each generation.
- The Android Gradle plugin's `test_config.properties` refers to the merged manifest,
  assets, and resource APK by paths relative to the module. These files are copied, and
  the references are rewritten to the generation's copies.
- The generation ID is a hash of the contents, the Java executable, and the JVM
  arguments. Building identical contents again reuses the same generation.
- A generation appears under its final name by an atomic rename. A failed build
  publishes nothing.
- JVM arguments that refer into the project are refused rather than silently left
  pointing at mutable files.

Each project directory has a **current generation**, the last one it published, so
two worktrees of one repository have independent current generations. An instance
keeps the generation it started from: later builds, failed builds, and
`gradle clean` cannot change the code it runs. Every driver result carries the
generation ID in `identity.build`.

## Leases

```sh
eval "$(noemu acquire)"     # export NOEMU_LEASE=...; waits within the limits
noemu screen                # any command acts on the leased instance
noemu sleep; noemu wake
noemu release               # destroys the instance; nothing is reused
```

`acquire --json` prints the instance, lease, generation, and expiry. An exclusive
instance refuses every request without its lease secret, including by numeric ID.
`spawn` keeps its earlier contract: it returns a shared instance that any local
caller can use by ID, so prefer `acquire` when several agents share a machine.

A lease expires after 30 minutes without an action; the instance is then destroyed.
Released instances are destroyed and their directories deleted, so a replacement
always starts clean. Each instance runs in its own working directory and temporary
directory, so relative paths and app storage stay inside it.

Actions on one instance run one at a time; different instances run concurrently.
An action that exceeds its deadline terminates the instance and reports
`deadline_exceeded`. The app may have done part of the work, and a cooperative
deadline cannot interrupt arbitrary app code.

## Limits and admission

| Setting | Default | Variable |
| --- | --- | --- |
| Leased, awake instances | 2 | `NOEMU_MAX_ACTIVE` |
| JVMs of any state | 4 | `NOEMU_MAX_JVMS` |
| Concurrent cold starts | 1 | `NOEMU_MAX_COLD` |
| Warm instances of the last build | 1 | `NOEMU_WARM` |
| Memory budget | smaller of 4 GiB and 40% of available memory | `NOEMU_MEMORY_MIB` |
| Charge for an unmeasured JVM | 768 MiB | `NOEMU_RESERVE_MIB` |
| Lease expiry | 1800 s | `NOEMU_LEASE_SECONDS` |

Available memory includes what the fleet already holds, and a cgroup v2
`memory.max` limit applies when set. Sleeping and warm JVMs count against the JVM
limit and the memory budget, but not against active instances. Requests are served
strictly in arrival order. When a JVM would not fit, idle warm instances are
destroyed first. A request that cannot be served in time fails with
`capacity_timeout`, stating active instances, JVMs, memory, and queue length. A
client that disconnects leaves the queue.

Settings are read when the supervisor starts; `noemu shutdown` stops it without
stopping instances.

## Recovery and cleanup

SQLite (WAL mode) records generations, each project's current generation, and every
instance with its state and lease. Queues and per-instance locks exist only in
memory. After a crash, a new supervisor adopts instances whose recorded process
identity (PID, creation time, owner) still verifies; a reused PID is never
signalled. Instances that were starting, or whose process is gone, are recorded as
failed.

`noemu gc` and every publish keep the three latest generations of each project, every
generation in use, and the logs of the eight latest failures. They remove abandoned
partial builds and dependency blobs that no generation links to.

## State and transport

State lives in `$NOEMU_HOME`, else `$XDG_STATE_HOME/noemu`, else
`~/.local/state/noemu`. The directory must belong to the user and be inaccessible to
others. The supervisor listens on a Unix socket in it (mode 0600). On Linux it also
rejects peers of another user (`SO_PEERCRED`). Requests and replies are one JSON
object per line, at most 8 MiB. Failures carry the same stable codes as driver
results.

Within the user account this is a coordination boundary, not a security boundary:
same-user programs can read the state directory and connect to app instances
directly.

## Limitations

- Linux is validated. macOS has not been validated, and native Windows is not
  supported.
- Process identity on macOS lacks Linux's pidfd pinning (see VALIDATION.md).
- A generation copies only what the recorded launch uses. Files the app reads from
  absolute project paths at run time are not snapshotted.
- The performance targets in ROADMAP.md have not been measured against this
  supervisor.
