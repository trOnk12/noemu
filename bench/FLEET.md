# Many app instances from one build (fleet spike, 2026-10-26)

Prompted by a developer who caps his coding agents at two Android emulators at a time and wanted to run apps on
Robolectric instead. The question: how many independent app sessions fit on one machine when noemu builds once and
starts each app JVM from the recorded command (first `fleet.py`, now `noemu pool` / `spawn`), with no Gradle per session?

Machine: the cloud container noemu was developed in (4 cores, 16 GB, no KVM). App: this repo's own `demo`
(Compose Multiplatform, `androidTarget`), which also needed noemu to support projects on the JUnit Platform.

| Sessions started at once | Up after | Memory per session | Total | Tap + new screen (with all running) |
| --- | --- | --- | --- | --- |
| 1 (through Gradle, for reference) | 30-49 s | app JVM about 540 MB, plus Gradle daemon about 1.3 GB and Kotlin daemon about 570 MB | about 2.4 GB | 4.4 s |
| 1 (fleet) | 25 s | 470 MB | 0.5 GB | |
| 6 (fleet) | 53-57 s | 496-592 MB | about 3 GB | each instance tapped a different checkbox and kept its own state |
| 12 (fleet) | 85-95 s | 483-582 MB | 6.4 GB | 4.7-4.8 s |

Start-up is CPU-bound (load average 20 on 4 cores while 12 start); once up, interaction didn't slow down. At rest the
12 used about 2.5 cores together (the demo animates a playback timeline; a static screen should idle lower).

Not measured here: emulators (no KVM in this container), so no side-by-side number; a larger app; how many agent tasks
the JVM can answer that otherwise need an emulator (Robolectric has no Play services, camera, real GPU or WebView,
and `kill` keeps singletons).

## Spawn, sleep, destroy (warm pool, 2026-10-26)

What the developer asked for next: "something that is quick, an agent can spawn fast and destroy/put to sleep fast".
`pool N` starts instances and stops their JVMs (SIGSTOP: no CPU, memory kept); `take` (now `noemu spawn`) wakes one. Measured on
the same machine while two corpus scans ran on it (load average about 13 on 4 cores):

| Operation | Time |
| --- | --- |
| Fill the pool (full start, in the background) | 66-73 s for 3 under load; 25 s alone on an idle machine |
| Spawn (`take`: wake a pooled instance) + read its screen | about 0.4 s |
| Sleep | about 50 ms (including starting the script) |
| Wake + screen, state kept (the tapped checkbox still selected) | 0.38 s |
| Destroy (`kill`, SIGKILL) | 65 ms |

Where a full start goes (one start under the same load): Robolectric's Android environment ready after 25.4 s, app
launched after 36.1 s, screen settled and listening after 51.3 s; the first half is identical for every instance, so
a pool, or a checkpoint of a warmed JVM, removes it from the agent's path. A tap answered in 4-5 s on this demo
because its screen never settles (a running clock): the driver waits up to 4 s for two unchanged reads; a static
screen settles in about 1 s.

## In the CLI (2026-10-26)

The fleet moved into `noemu` itself (`build`, `pool`, `spawn`, `sleep`, `wake`, `destroy`, `list`, and `--id` /
`NOEMU_ID` for every session command). Checked on the same app: two agents calling `spawn` at the same moment got
different instances and the pool refilled itself; `destroy` removes the instance's temporary app data. Tap answered,
`NOEMU_SETTLE_MS=1000`, under load average 12: 1.1-1.3 s without per-action screenshots, about 1.45 s with (the
first tap on an instance about 1 s slower: a cold JIT).

## Settling with numbers masked (2026-10-26)

An action used to wait until two reads of the screen matched; a running clock never matched, so every tap on the demo
waited the full 4 s. The settle check now masks digits. Same demo, default settings, `NOEMU_SHOTS=0`: taps answered
in 627-830 ms (4 taps), each landing (the tapped checkbox selected) while the clock kept running.
