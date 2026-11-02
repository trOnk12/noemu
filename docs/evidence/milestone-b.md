# Milestone B evidence

**Result: the exit gate passed three consecutive runs** at
[`4b64127`](https://github.com/trOnk12/noemu/commit/4b641277652770ec22c90801d92a0ae2d8a037ba). Each run cloned that commit, verified the checked-out revision,
resolved dependencies afresh, and used a private fleet directory.

| Check | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| Python suite (154 tests) | pass, 45s | pass, 45s | pass, 45s |
| Wheel build and clean installation | pass | pass | pass |
| Installed `tests/mcp_stdio.py` | pass | pass | pass |
| ProtocolTest, Views and Compose (5 tests each) | pass | pass | pass |
| Live driver, Views (10 tests) | pass, 58s | pass, 56s | pass, 58s |
| Live driver, Compose (10 tests) | pass, 63s | pass, 63s | pass, 63s |
| Live fleet across worktrees (6 tests) | pass, 101s | pass, 103s | pass, 103s |

Host: WSL2 Ubuntu 22.04, Linux 6.6.87, 6 vCPU, 7.8 GiB; Python 3.10.12, OpenJDK
21.0.13, Gradle 8.11.1, mcp 1.30.0, psutil 7.2.2. This is not the roadmap's reference
hardware (4 vCPU, 16 GiB).

## What the exit gate asserts

The live fleet test (`tests/integration_fleet.py`) runs real builds and app JVMs in
real git worktrees, each worktree with different app code:

| Exit criterion | Assertion |
| --- | --- |
| Agents cannot share an exclusive instance | Two agents acquiring concurrently from different worktrees get different instances. Using the other agent's instance by ID fails with `lease_required`. |
| Limits are not exceeded | A third acquisition fails with `capacity_timeout` and states `2/2 active`. Status shows the JVM, active, and memory figures within their limits. |
| No data crosses instance boundaries | An instance writes app files, shared preferences, and a relative-path file. A concurrent instance and a later replacement see none of them, and nothing is written into the project. |
| No accidental generation | Each instance runs its worktree's code, and `identity.build` equals its lease's generation. A rebuild, a failed build, and deleting `build/` leave a running instance on its original code; a new lease gets the new generation. |
| Processes end | After release, the instance's process no longer exists. |

The stub-driver suite (`tests/test_supervisor.py`, 53 tests) covers what is
impractical with real JVMs: strict FIFO order, a client leaving the queue, lease
expiry, unresponsive apps, crashes, sleeping JVMs counting against limits, memory
admission, warm pool behavior, retention, supervisor crashes with adoption, PID reuse,
and replacement of an outdated supervisor. The stub is a real process speaking the
driver protocol, signalled and cleaned up exactly like an app JVM.

**Mutation check.** Running instances in the project directory, as the earlier fleet
did, was reintroduced on purpose. The data-boundary test failed and named the leak:
`relative=token` was visible to a second instance. The earlier fleet therefore
leaked relative-path files between instances.

## Measurements

The 100-cycle samples were taken at `6169b3f`; the final commit changed only shutdown and
pool-refill handling, and a 10-cycle rerun at `4b64127` matched within 1 ms at p95 for
every fleet operation and left no JVM behind. Samples come from
`bench/fleet_latency.py` with the Views fixture and `NOEMU_SHOTS=0`. Each operation
is timed separately over 100 cycles, and every cycle completed.

| Operation | p50 | p95 | max |
| --- | --- | --- | --- |
| Warm acquisition | 4.5 ms | 5.3 ms | 6.2 ms |
| First usable observation after acquisition | 24.0 ms | 29.8 ms | 32.7 ms |
| Simple semantic action (`tap`, including settling) | 472.6 ms | 474.3 ms | 476.9 ms |
| Sleep | 2.7 ms | 5.3 ms | 6.6 ms |
| Wake to usable observation | 7.9 ms | 10.8 ms | 15.4 ms |
| Release with confirmed process exit | 35.7 ms | 38.5 ms | 41.5 ms |
| Cold start of a pool refill (not on the request path) | 4.06 s | 4.14 s | 4.25 s |

On this host, every p95 is within the roadmap's 1-second targets for warm
acquisition, wake to usable observation, simple semantic actions, and confirmed
destruction. These are not measurements on the reference hardware. The action time
is dominated by the driver's settling wait, not by the fleet.

`bench/fleet_soak.py` ran **1,000 acquire, act, release cycles by three concurrent
agents** in 23.7 s through the supervisor and stub instances. It recorded no failures,
no instance seeing an earlier instance's data, no orphan process, no remaining
instance or directory, and peaks of 2 active instances and 2 JVMs. The cycles used
stub instances, not app JVMs, so this checks the supervisor's lifecycle handling,
not Robolectric startup.

## Defects found on the way

| Defect | How it surfaced | Fix |
| --- | --- | --- |
| Re-entrant scheduling: evicting a warm instance re-ran the scheduler inside itself | Stub test crashed with `pop from empty list`; a different interleaving could double-launch | Non-reentrant scheduler that repeats its pass |
| Unexpected errors dropped the client connection | Same test: the client saw `connection_lost` | Returned as `internal_error` with the log location |
| A new build's warm refill used the old generation | Stub test | The pool follows the project's new generation |
| `pool 0` left warm JVMs running | Soak smoke run | Surplus warm instances are destroyed |
| `shutdown` left warm JVMs without an owner | Leftover processes after a manual run | Shutdown ends warm instances |
| Refills still starting outlived `shutdown`, and late refills outlived `pool 0` | Two app JVMs left after the 100-cycle latency run | Shutdown ends starting instances; late surplus refills are destroyed |
| Gate evidence missed instance logs, and gate cleanup signalled bare PIDs | Review | Supervisor logs collected; cleanup verifies identity |

## Product review

**Which workflow becomes easier?** Several agents, each in its own git worktree, can
run their own revision of an app at the same time without sharing an instance or
disturbing each other's builds. `examples/rotation-fixes` runs a broken app and two
candidate fixes concurrently and reports which fix keeps its state through rotation.
Including three builds, it took 25 seconds here. Only the fix that saves the state
passes. The decoy restores the screen text and fails on the next tap. The live gate
asserts these verdicts.

**What advantage over direct Robolectric or a capped emulator?** Measured: isolated,
leased instances of different revisions run concurrently within enforced limits.
Every result names the immutable build it ran, and warm acquisition takes
milliseconds. Direct Robolectric runs one test JVM per Gradle invocation and has no
cross-worktree isolation or leases. Not measured: any comparison with an emulator
workflow; that requires KVM-capable hardware and is part of the performance evidence
still to collect.

**What evidence supports this, including negative cases?** The three gate runs, the
mutation check, the soak, and the 100-cycle measurements above. Negative cases:
refusal without a lease, capacity timeouts with reasons, a failed build, deleted
build outputs, crashes, and unresponsive apps. Limitations: Linux only; macOS not run;
no Android comparison; the soak used stub instances; the measurements were not on the
reference hardware; `spawn` instances are shared by design.

**What is the smallest next improvement?** The comparison shows what the JVM
simulation does. It cannot show that Android agrees. Milestone C runs the same
scenarios on instrumented Android fixtures and reports every discrepancy.

The roadmap calls B the first external pilot release. Recruiting pilot users is
outside the authorized scope, so no external pilot has run.
