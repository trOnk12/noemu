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
The same change shortened a cold start, which ends by waiting for the first screen to settle: `spawn` with an empty
pool took 10 s (load average 3.7): Robolectric's Android ready after 6.2 s, the app launched after 9.1 s, settled and
listening after 9.8 s. Before, the settle step alone ran out its 15 s on this demo.

## JVM options for small instances (2026-10-26)

Same demo, one instance each, after 3 taps (all landing): default JVM 468 MB; `-Xmx512m` 464 MB; serial GC with the
fast JIT tier only (`-XX:+UseSerialGC -XX:TieredStopAtLevel=1`) 388 MB, the same at `-Xmx384m`. Ready after 9.1-9.7 s
with those options against 10.3-10.8 s without; taps 0.6-0.7 s either way. They are the fleet's default now.

## Twelve again, with everything above (2026-10-26)

`pool 12` (default settings, `NOEMU_SHOTS=0`): all 12 asleep in the pool after 56 s (before: about 90 s), 4.6 GB
together, 385-389 MB each (before: 6.4 GB). Then three agents' `spawn` + tap while the pool refilled (load average 13
on 4 cores): taps answered in 834-928 ms. Nothing new in the shared temporary directory.

## Idle instances end themselves (2026-10-26)

`NOEMU_IDLE_MIN=0.25`: a spawned instance left without commands ended after 16 s; a pooled one asleep for 40 s was
alive when spawned, answered a tap, and ended 16 s after it. The driver counts idle time in 2 s ticks and skips a tick
that took far longer (the process was frozen), so time in the pool isn't idle time.

## Tried, not adopted (2026-10-27)

Same demo and machine, which was also running a 9-process batch job (load average 8 on 4 cores), so the times vary.

- **Class data sharing** (a dynamic CDS archive of the instance's classes, 100 MB): cold start 7.9-9.8 s against
  9.6-12.6 s without, alternating three of each; 375 MB against 386 MB. CDS refuses classpath directories, so Gradle's
  class directories had to be packed into jars first, and the archive is only written when an instance exits by
  itself. Robolectric loads Android's classes (about 6 s of the start) through its own class loader, which the
  archive doesn't cover. About 1.5 s off a cold start, when a spawn from the pool takes 0.4 s, isn't worth that.
- **Giving heap back before sleeping:** on the relay screen, 469 MB; after a full GC 421 MB with the serial
  collector's defaults and 423 MB with `-XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=20`. Of what remains, the live
  heap is about 100 MB and class metadata about 94 MB.
- **Checkpoint/restore** (CRIU or CRaC, to start instances from a snapshot of a running one): not installed here; it
  needs Linux with checkpoint privileges, or a CRaC JDK, and doesn't run on macOS.

## Instances on one real server (2026-10-27)

The demo's relay screen sends 60 observations over HTTP to the repository's local relay (`:relay:run`) and reads them
back, on background coroutines; the screen updates between agent commands. One instance: `SUCCESS`, 60 of 60, and the
relay held the session's 60 observations. A second instance then **failed**: "Message android-1ajz7wg:0 was already
accepted with another payload". The demo names its session after `SystemClock.elapsedRealtimeNanos()`, and every
instance had booted at Robolectric's 100 ms and been advanced the same way, so the second claimed the first one's
session. Each instance now boots at a random uptime (a Robolectric `TestEnvironmentLifecyclePlugin`, run before the
app is created): three instances in a row each got their own session and all three succeeded. One put to sleep for
30 s at 9 of 60 accepted woke, carried on and finished 60 of 60.

## Real time (2026-10-27)

A random uptime fixed the repeated IDs but left two things wrong.
- **The time of day.** With the paused looper, `System.currentTimeMillis()` in Android's own code is the uptime. In a
  Robolectric test on the demo module, `DateUtils.getRelativeTimeSpanString(a minute ago)` returned "Oct 27, 2026" and
  `Notification.when` was 172417001 (January 1970).
- **Time between commands.** Virtual time moved only while an action settled. Two `screen` reads 10 s apart showed
  the same demo playback time.

Now each instance's clock starts at the real time (the uptime, as Robolectric ties the two), plus a random fraction of
a millisecond. Before each command, and before the app launches, it catches up with the real time. Results:
- `DateUtils` gave "1 minute ago", and `Notification.when` was the real time.
- 10 s between two `screen` reads moved the demo's playback from `t = 800 ms` to `t = 3775 ms`.
- An instance asleep in the pool for 60 s caught up 71.9 s on its first command and answered `spawn` + `screen` in
  1.0 s.
- Between commands the clock was 0.1-1.4 s behind the real time (settling runs slower than real time); it was never
  ahead.
- Three instances on one relay each got their own session, and all three succeeded.
- Taps, the old build against the new one, alternating, with screenshots off, at load average 7: 564-1688 (median 630)
  against 572-760 ms (median 680), within the noise.

A command sent to an asleep instance used to hang for 120 s (the kernel accepts a frozen JVM's connections) and end in
a traceback. It is now refused in 70 ms, with the command to run.
