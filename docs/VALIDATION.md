# Validation record

## Scope and provenance

Work started from upstream `c97e16542a8465df6d0e5389cb6ea34f5296b3c2` on
2026-10-27. The shell could not clone through its configured proxy. The GitHub
connector supplied a pinned source snapshot; all 40 retrieved text files were
verified against their Git blob SHA-1 values. The existing binary demo GIF was not
needed for editing and was not downloaded. It is not removed from the upstream tree.

The baseline was **23 Python tests passing**. The first safety work unit passed
**30 tests**, including real child-process checks for signaling and cleanup.
The expanded suite passed **44 tests in 0.551 seconds** in this Linux environment.
A wheel built successfully and its installed CLI, MCP import, Kotlin sources, init
script, and service resources passed a smoke check outside the source directory.
That offline smoke environment reused installed dependencies; it is not a clean
dependency-resolution test. No Android-conformance or performance result is claimed.

## Gate status

| Check | Status |
| --- | --- |
| Existing Python regressions and new safety/protocol/MCP/benchmark tests | Passed locally (44 tests) |
| Wheel build and installed-package resource smoke check | Passed locally; existing dependencies reused |
| Fully isolated install with freshly resolved dependencies | Pending CI |
| Kotlin compilation and protocol contracts | Pending CI; no local Gradle/Android SDK |
| Live Views and Compose driver checks | Pending CI; no local Gradle/Android SDK |
| Milestone A | Incomplete until required checks pass and failures are resolved |
| Milestones B–H | Not started; gated by A |

The managed shell blocks networking, including asyncio's cross-thread socket wakeup.
A test-only event-loop timer allows in-process MCP worker callbacks to run in that
sandbox. Production MCP code does not contain that timer. The test suite exercises
the official SDK session and validation handlers, but this is not a substitute for
an installed stdio-client integration check in a normal environment.

## Material limitations

- The JSON protocol changes both client and driver. Old instances must be rebuilt
  and relaunched; there is no silent text-protocol fallback.
- Linux process signaling uses a pinned pidfd plus creation-time/owner checks when
  available. macOS uses psutil's creation-time check; kernel-atomic pidfd semantics
  are not available there. Local process regressions ran on Linux only.
- Legacy fleet metadata and runtime paths remain mutable and project-local. Resource
  scheduling, leases, immutable builds, recovery, and bounded fleet retention are B.
- Driver deadlines are cooperative. Uninterruptible app code may continue after a
  client timeout; the outcome is reported as inconclusive, and retries are not automatic.
- UI actions use semantics rather than physical hit testing. Occlusion, lifecycle,
  rendering, permission, and Android-version conformance require C's independent
  Android corpus. Kotlin modifications must pass the fixture gate before release.
- Several existing runtime approximations still require the conformance-guided
  adapter review in D. No claim of universal fidelity or parity with Android is made.
- `restore-activity` and its legacy `kill` alias preserve JVM statics. Genuine cold
  restart and Android task restoration remain H.

CI retains logs and incomplete fixture runs as artifacts. A workflow definition or
an unexecuted test is never counted as a passing result.
