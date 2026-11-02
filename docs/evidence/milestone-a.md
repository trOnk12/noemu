# Milestone A evidence

**Result: the hardened exit gate passed three consecutive runs** at
[`57e8756`](https://github.com/trOnk12/noemu/commit/57e8756a7994650a26dda06e8debd7eabc953990).
Each run cloned that commit, verified the checked-out revision, and resolved
dependencies afresh.

| Check | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| Python suite (109 tests) | pass | pass | pass |
| Wheel build and clean installation | pass | pass | pass |
| Installed `tests/mcp_stdio.py`, outside the checkout | pass | pass | pass |
| ProtocolTest, Views (5 tests) | pass, 21 s | pass, 20 s | pass, 20 s |
| ProtocolTest, Compose (5 tests) | pass, 25 s | pass, 24 s | pass, 24 s |
| Live driver, Views (10 tests) | pass, 57 s | pass, 58 s | pass, 58 s |
| Live driver, Compose (10 tests) | pass, 62 s | pass, 63 s | pass, 63 s |

Same host and versions as below; mcp 1.30.0 and psutil 7.2.2 were freshly installed.

### The hardened gate found a process-identity defect

The first full run of the [hardened gate](gate-hardening.md), at
[`53f93d4`](https://github.com/trOnk12/noemu/commit/53f93d4e826508e72c6dc32b61f9cd41f25cff87),
**failed**: three live driver tests reported `process identity changed` for instances
that were running. The recorded identities showed 3 of 20 instances affected.
Identity compared psutil's `create_time()` exactly. On Linux, psutil computes it from
the boot time in `/proc/stat`, which is wall-clock time minus uptime. A clock step or
slew (NTP, or a VM syncing with its host) moves that value, so one process can read
differently. A flip was not caught directly in a short sample, so this cause is
inferred from the mechanism and the failing records rather than observed.

`57e8756` identifies processes on Linux by the kernel's start ticks
(`/proc/<pid>/stat`, field 22). These never change for a process. Records without
them still require an identical creation time. The regressions failed before the fix
and pass after it, and the three runs above passed.

During this work, two gate runs were invalid because the repository's working tree
switched branches between runs, so `scripts/gate.sh` itself came from another
revision. Those runs are not counted. The three runs above used a dedicated checkout
of `57e8756`. **Run the gate from a checkout that stays on the revision under test.**

## Earlier runs at `62efbba`

Before the gate hardening, the same checks passed three consecutive runs at
[`62efbba`](https://github.com/trOnk12/noemu/commit/62efbba3e3b0643d12e57e10dbdb2717c3881254).

| Check | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| Python suite (70 tests at that commit) | pass | pass | pass |
| Wheel build and clean installation | pass | pass | pass |
| Installed `tests/mcp_stdio.py`, outside the checkout | pass | pass | pass |
| ProtocolTest, Views (5 tests) | pass, 33 s | pass, 22 s | pass, 25 s |
| ProtocolTest, Compose (5 tests) | pass, 33 s | pass, 23 s | pass, 25 s |
| Live driver, Views (10 tests) | pass, 66 s | pass, 62 s | pass, 63 s |
| Live driver, Compose (10 tests) | pass, 65 s | pass, 72 s | pass, 72 s |

Host: WSL2 Ubuntu 22.04, 6 vCPU, 7.7 GiB, not the roadmap's reference hardware.
Software versions are in [VALIDATION.md](../VALIDATION.md). No test was skipped or
weakened, and the timings are not performance claims.

## Defects found by the first real runs

Inspection and the Python suite had not exposed any of these.

| Defect | Effect before the fix | Evidence after |
| --- | --- | --- |
| Driver used `java.lang.management` directly | The driver did not compile against `android.jar`, so no Android project could build it | ProtocolTest compiles and passes on both fixtures |
| No frame between `visible()` and `windowFocusChanged()` in `recreate()` | Every recreating rotation failed; the earlier suppression hid this and cut the lifecycle short | Rotation tests pass, and the fixture's own focus crash is reported |
| Compose finishes layout in `dispatchDraw`, and nothing draws without a screenshot | After a scroll, observations reported old positions while the operation reported success | Horizontal scroll test passes with `NOEMU_SHOTS=0` |
| Reflective calls wrapped app exceptions | A Compose crash was reported as `InvocationTargetException: null`, losing the app's message | The CLI reports the fixture's crash message |

The stale Compose positions took a wrong first hypothesis to find. Instrumentation
showed the scroll offset reaching 1200 of 3567 while element bounds did not move. The
action had worked; the observation was wrong. An explicit `shot` then corrected the
bounds, which identified the missing draw.

## Product review

**Which workflow becomes easier?** An agent can now build a real Android project into
the driver, rotate it, and receive state-loss evidence that distinguishes a saved
field from a lost one. Before this gate, the driver did not compile.

**What advantage over direct Robolectric or a capped emulator?** Measured: one
command gives structured observations and typed failures, and a crash in app code
surfaces with its own message as a nonzero exit. Measured: no emulator or KVM is
needed. Hypothesis: this is cheaper or faster overall than an emulator workflow; no
comparison has run against the current code.

**What evidence supports this, including negative cases?** The live tests assert
exact error codes for disabled, ambiguous, missing, and stale targets, and exact
crash messages. A deliberate crash must fail, and `FocusCrashActivity` must fail
during rotation. Known limits: nothing here compares with Android, activities do not
receive focus at first launch, and `RecreationFrame` is a workaround rather than a fix
in Robolectric. macOS and GitHub Actions have not run.

**What is the smallest next improvement?** Milestone B. Agents in separate worktrees
cannot yet use the fleet safely, because instances share mutable build outputs.
