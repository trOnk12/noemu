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

| Check | Status |
| --- | --- |
| Existing Python regressions and new safety/protocol/MCP/benchmark tests | Passed locally (70 tests) |
| Wheel build and installed-package resource smoke check | Passed locally; existing dependencies reused |
| Installed MCP stdio integration | Failed locally: initialization response timed out after 10 seconds in the restricted sandbox |
| Fully isolated install with freshly resolved dependencies | Pending CI |
| Kotlin compilation and protocol contracts | Pending CI; no local Gradle/Android SDK |
| Live Views and Compose driver checks | Pending CI; no local Gradle/Android SDK |
| Milestone A | Incomplete until required checks pass and failures are resolved |
| Milestones B–H | Not started; gated by A |

## Published branch and CI blocker

The implementation is published in [PR #1](https://github.com/trOnk12/noemu/pull/1)
on `codex/noemu-foundation`, preserving upstream history and the existing demo GIF.
GitHub Actions [run 41296966023](https://github.com/trOnk12/noemu/actions/runs/41296966023)
did not start its test jobs: GitHub reports that the account is locked due to a
billing issue. Cancelled matrix jobs are not passing checks. This prevents the
required Kotlin, live-driver, and isolated-install gate from establishing an outcome.

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
integer types for protocol fields. **The parser change and Kotlin regressions are
unverified:** the local fixture gate still stops at its missing-wrapper prerequisite.

The driver observation code also now catches only `Resources.NotFoundException`
when a View ID has no compiled resource name. Generated IDs retain their observation
references and receive no invented resource selector. The Views fixture uses both
generated and resource IDs, with an integration regression covering observation and
typing. **This Kotlin change is unverified.** The attempted fixture run failed its
prerequisite check (`fixture wrapper missing`) and executed zero driver tests.

### Rotation exception suppression

Source inspection found that rotation suppressed any `NullPointerException` with
a `windowFocusChanged` method anywhere in its stack. That is not evidence that the
exception is harmless: Robolectric 4.16's
[ActivityController.recreate](https://github.com/robolectric/robolectric/blob/robolectric-4.16/shadows/framework/src/main/java/org/robolectric/android/controller/ActivityController.java)
calls `windowFocusChanged(true)` after making the recreated activity visible,
and focus dispatch reaches application callbacks.

The driver now lets this exception reach its ordinary failure result. Both fixture
apps include `FocusCrashActivity`, which succeeds at launch and deliberately throws
on focus after recreation. The integration regression requires the precise fixture
crash to remain visible as `execution_error`, rather than accepting any failed
operation. **This Kotlin change and regression have not run.** The new local gate
attempt again failed at the missing-wrapper prerequisite and ran zero driver tests.
If Robolectric itself throws during focus dispatch, rotation will now correctly
fail too; compatibility work must reproduce and fix that defect instead of hiding it.

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

Run both checks with the installed wheel's Python interpreter, outside the checkout:

```sh
"$check_dir/venv/bin/python" "$repo/tests/mcp_stdio.py"
"$check_dir/venv/bin/python" "$repo/tests/mcp_sandbox_diagnostic.py"
```

The variables and clean-install instructions are in [REVIEW.md](REVIEW.md). The
first command is the required gate. The second changes scheduling and **does not
replace or pass that gate**. The normal launch still times out at initialization
in this sandbox. CI now also runs the unmodified subprocess test against the fresh
wheel installation; its result remains pending while GitHub jobs cannot start.

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
