# Validation record

## Scope and provenance

Work started from upstream `c97e16542a8465df6d0e5389cb6ea34f5296b3c2` on
2026-10-27. The shell could not clone through its configured proxy. The GitHub
connector supplied a pinned source snapshot; all 40 retrieved text files were
verified against their Git blob SHA-1 values. The existing binary demo GIF was not
needed for editing and was not downloaded. It is not removed from the upstream tree.

The baseline was **23 Python tests passing**. The first safety work unit passed
**30 tests**, including real child-process checks for signaling and cleanup.
The initial expanded suite passed **44 tests in 0.551 seconds**. Follow-up
regressions on 2026-11-01 passed **70 tests in 0.403 seconds** in this Linux environment.
A wheel built successfully and its installed CLI, MCP import, Kotlin sources, init
script, and service resources passed a smoke check outside the source directory.
That offline smoke environment reused installed dependencies; it is not a clean
dependency-resolution test. No Android-conformance or performance result is claimed.

## Gate status

The hardened gate passed three consecutive clean runs at `57e8756`; see
[the evidence](evidence/milestone-a.md). Its first run, at `53f93d4`, failed and
exposed a process-identity defect that `57e8756` fixes.

| Check | Status |
| --- | --- |
| Python regressions | Passed (109 tests); 3 of 3 at `57e8756` |
| Clean wheel installation with fresh dependencies | Passed; 3 of 3 |
| Installed MCP stdio integration (`tests/mcp_stdio.py`) | Passed; 3 of 3 |
| Kotlin compilation and ProtocolTest, both fixtures | Passed (5 tests each); 3 of 3 |
| Live Views and Compose driver checks | Passed (10 tests each); 3 of 3 |
| Milestone A | **Gate passed** |
| macOS | Not run |
| GitHub Actions | Not run; jobs cannot start (account billing lock) |
| Android conformance | Not measured; milestone C |
| Milestone B | In progress |
| Milestones C–H | Not started |

Environment: WSL2 Ubuntu 22.04 on Linux 6.6.87 (6 vCPU, 7.7 GiB), Python 3.10.12,
Temurin OpenJDK 21.0.13, Gradle 8.11.1, Android platform 35, build tools 35.0.0, mcp
1.30.0, psutil 7.2.2. Command: `scripts/gate.sh`.

The earlier `Installed MCP stdio` timeouts below came from a sandbox that blocks
`socketpair`. The unmodified test passes on a normal Linux kernel. Entries below this
point are the historical record of work done before these gates could run.

## Follow-up regressions

- Malformed result fields, duplicate JSON keys, deeply nested JSON, and malformed
  observation references now fail with typed protocol errors instead of becoming
  successful evidence or escaping validation.
- The client subtracts connection time before sending; an expired connection
  budget cannot dispatch a mutation.
- MCP retains structured driver evidence on success and failure and exposes
  explicit scroll targets, rotation counts, activity restoration, and bounded crawls.
  Invalid network options cannot silently become a different request.
- A raw invalid UTF-8 byte sequence reproduced an MCP input-decoder exception.
  Byte framing now rejects that line and allows the following request through.
  Oversized-line draining, UTF-8 byte limits, and EOF behavior have unit regressions.
- An unknown build option reproduced deletion of an existing launch configuration.
  Option validation now runs before metadata or logs are changed.
- A 600,000-character result included as both text and structured evidence produced
  a 1,200,122-byte SDK reply, exceeding the 1 MiB limit. Final MCP reply framing now
  replaces oversized output with an explicit inconclusive error and permits later
  replies. Tests also cover multibyte UTF-8 and an oversized request ID.
- Missing/invalid driver ports and socket failures now produce typed errors.
  Regressions distinguish connection refusal before dispatch from failures during
  sending or receiving, where the execution outcome remains unknown.
- Resident memory now comes from the verified process through psutil. An unreadable
  measurement is `memory unknown` rather than zero. Real Linux child-process tests
  cover running and sleeping processes, identity mismatch, and denied measurement.
  macOS execution remains unverified.
- Python command/result validation now rejects unpaired Unicode surrogates. The
  regressions failed before the fix and pass afterward; valid emoji, Polish and
  Japanese text, and escaped whitespace still round-trip without replacement.
- MCP output now encodes UTF-8 directly to stdout's binary buffer, regardless of
  the inherited locale or `PYTHONIOENCODING`. A timer-assisted installed-server
  experiment completed initialization but timed out when echoing a Polish,
  Japanese, and emoji request ID under ASCII output settings. The encoding unit
  regression and the same diagnostic pass after the fix. The normal subprocess
  gate remains blocked as described below.

The driver request parser now uses strict `JsonReader` parsing before command
validation, with duplicate-field checks, complete-document consumption, a nesting
limit, and Unicode validation. Kotlin regressions cover these cases and preserve
integer types for protocol fields. ProtocolTest has since run; see Gate status.

The driver observation code also now catches only `Resources.NotFoundException`
when a View ID has no compiled resource name. Generated IDs retain their observation
references and receive no invented resource selector. The Views fixture uses both
generated and resource IDs, with an integration regression covering observation and
typing. The fixture gate has since passed.

### Rotation exception suppression

Source inspection found that rotation suppressed any `NullPointerException` with
a `windowFocusChanged` method anywhere in its stack. That is not evidence that the
exception is harmless: Robolectric 4.16's
[ActivityController.recreate](https://github.com/robolectric/robolectric/blob/robolectric-4.16/shadows/framework/src/main/java/org/robolectric/android/controller/ActivityController.java)
calls `windowFocusChanged(true)` after making the recreated activity visible,
and focus dispatch reaches application callbacks.

The driver now lets this exception reach its ordinary failure result. Both fixture
apps include `FocusCrashActivity`, which succeeds at launch and deliberately throws
on focus after recreation.

The first live run confirmed that the suppression was hiding a defect. **Every**
recreating rotation failed with a `NullPointerException` at
`ActivityController.windowFocusChanged(ActivityController.java:257)`. The cause is in
the interaction between the driver and Robolectric, not in the app: the driver pauses
the Choreographer, so a frame runs only when the clock advances, and attaching a new
window takes a frame. `recreate()` calls `visible()` and then `windowFocusChanged(true)`
with nothing advancing the clock in between, so `getViewRootImpl()` is null. Launch
never hit this because `setup()` does not deliver focus. The old suppression also
skipped focus delivery, `topActivityResumed(true)`, and restoration of the original
lifecycle state on every rotation.

`RecreationFrame` now gives the window exactly one frame during recreation. Unpausing
was rejected because `recreate()` idles the looper, and an unpaused Choreographer turns
an endless animation into an endless loop. `configurationChange` was rejected because
its source documents that retained fragments are not retained. `EndlessAnimationActivity`
guards the bound. `FocusCrashActivity` now reports its own exception, which proves that
focus reaches the app after recreation. This shadow is a workaround; milestone D
reviews it against milestone C's corpus.

The updated wheel was installed into a separate virtual environment reusing
available dependencies, and its CLI, imports, and packaged resources passed checks
from `/tmp`. The actual installed MCP stdio test was also attempted: it failed at
initialization with `MCP response deadline exceeded` after 10.021 seconds. The
framing unit tests deliberately use a synchronous reader; they do not count as a
passing subprocess integration test.

The managed shell blocks socket operations: a direct `socket.socketpair()` probe
raised `PermissionError` with errno 1. This also interferes with asyncio's
cross-thread wakeups. A test-only event-loop timer allows in-process MCP worker
callbacks to run in that sandbox. Production MCP code does not contain that timer.

To isolate this restriction, `tests/mcp_sandbox_diagnostic.py` runs the installed
server with a 10 ms timer and otherwise uses the same subprocess contract test.
After the UTF-8 fix, this **diagnostic** passed in **1.237 seconds**, covering actual
pipes, initialization, malformed JSON/UTF-8, draining an oversized line exceeding
2 MiB, tool discovery, an invalid tool argument, a multilingual ping ID under
ASCII Python output settings, and a zero exit status after stdin EOF. The test
client bounds reads and writes and includes child stderr on failures.

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
- Activities receive window focus after recreation but not at first launch, because
  `setup()` does not deliver it. This is a known difference from Android.
- UI actions use semantics rather than physical hit testing. Occlusion, lifecycle,
  rendering, permission, and Android-version conformance require C's independent
  Android corpus. Kotlin modifications must pass the fixture gate before release.
- Several existing runtime approximations still require the conformance-guided
  adapter review in D. No claim of universal fidelity or parity with Android is made.
- `restore-activity` and its legacy `kill` alias preserve JVM statics. Genuine cold
  restart and Android task restoration remain H.

CI retains logs and incomplete fixture runs as artifacts. A workflow definition or
an unexecuted test is never counted as a passing result.
