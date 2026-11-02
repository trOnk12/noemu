# Review and continuation guide

This guide is for maintainers and coding agents continuing the work. Describe
implemented behavior separately from verified behavior, and keep unfinished checks
visible. A merged PR does not establish a passing validation gate.

## Scope and current state

Milestone A's hardened gate passed three clean runs at `57e8756`; see
[its evidence](evidence/milestone-a.md). Milestone B's passed three at `4b64127`; see
[its evidence](evidence/milestone-b.md) and [SUPERVISOR.md](SUPERVISOR.md). Run the gate from a checkout that stays on
the revision under test: `scripts/gate.sh` itself comes from the working tree.
It covers process and instance safety, versioned JSON transport, truthful CLI/MCP
failures, structured observations, UI targeting, and the maintained fixtures. The
gate establishes JVM behavior on Linux, not Android conformance. macOS has not run.
GitHub Actions jobs cannot start because of an account billing issue, so
`scripts/gate.sh` is the record of truth; a workflow file proves nothing by itself.

Read [VALIDATION.md](VALIDATION.md), [PROTOCOL.md](PROTOCOL.md), and
[ROADMAP.md](ROADMAP.md) before extending the implementation.

## Checks to run

On Linux x86-64, without root:

```sh
scripts/bootstrap-toolchain.sh        # JDK 21, Gradle 8.11.1, SDK 35, in ~/tools/noemu
. ~/tools/noemu/env.sh
scripts/gate.sh [revision] [results-dir]
```

The gate clones one committed revision and creates fresh virtual environments, so
uncommitted files and local packages cannot affect the outcome. It runs the Python
suite, builds and installs the wheel, runs `tests/mcp_stdio.py` with the installed
interpreter outside the checkout, generates the fixture wrapper, then runs ProtocolTest
and the live driver tests on both fixtures using the installed wheel. Revision
resolution, clone/checkout, and virtual-environment failures stop immediately.
The cloned HEAD must match the requested commit before any check runs. Independent
checks continue after test failures; any failure produces a nonzero exit status.

Each check defaults to 900 seconds (`NOEMU_GATE_CHECK_SECONDS`); preparation and
environment discovery each get 300 seconds (`NOEMU_GATE_SETUP_SECONDS`). Artifact
capture gets 30 seconds. The command runner sends TERM to its process group, then
KILL after 5 seconds (`NOEMU_GATE_KILL_GRACE_SECONDS`), including descendants that
remain after the command leader exits. Overrides must be finite positive seconds,
at most 86400. A timeout records exit 124 and cannot pass the gate. INT/TERM
cancellation is forwarded to the active runner and leaves the run incomplete.
These are command deadlines, not a global hard wall-clock limit: processes that
detach into another session/group and uninterruptible kernel work are outside
this cleanup guarantee. In particular, separately launched NoEmu JVMs still use
their own verified process records; do not assume a timed-out gate destroyed them.

The results directory must be empty, so old evidence cannot be overwritten or mixed
into a new run. `environment.txt` records the requested and checked-out SHA;
`result.txt` remains `incomplete` until the checks finish. The installed wheel must
import from its own virtual environment; inherited `PYTHONPATH`/`PYTHONHOME` are removed.

After every check, selected fixture startup/build logs and JUnit XML are copied to
`artifacts/<check>/`, with paths, sizes, SHA-256 hashes, and collection errors in
`manifest.json`. App storage and native runtime extraction directories are excluded.
Limits per collection are 500 files, 2 MiB per file, and 16 MiB total. Oversized
artifacts retain their tail with a `.truncated` suffix. Missing prerequisites may
produce no fixture artifacts; the check's failure log still remains available.
Truncation, refused symlinks, or collection errors make the gate fail explicitly.

Successful runs remove their temporary clone after saving evidence. Failed or
interrupted runs retain that workspace and print its path and results directory.
Remove the reported workspace when diagnostics are no longer needed; repeated
failed runs retain separate workspaces. `dependency-inventory.log` records installed
Python dependencies. The gate does not retain screenshots or arbitrary app data in
its copied artifact bundle.

`tests/mcp_sandbox_diagnostic.py` is only a diagnostic for sandboxes without working
socket pairs. Its success never substitutes for `tests/mcp_stdio.py`. The driver uses
Robolectric 4.16 / API 36; these are JVM tests, not instrumented Android conformance.

Record the tested commit, runtime versions, command, exit status, and failure log.
Do not turn unavailable prerequisites or cancelled jobs into skips or passes.

## Review priorities

1. **Truthful outcomes.** Confirm that driver failures, background crashes, partial
   execution, malformed replies, and timeouts cannot become successful CLI/MCP
   results. Check the driver parser as well as Python validation.
   Run the focus-crash rotation regression on both fixture apps; a callback name
   in a stack trace must never turn a thrown exception into success.
2. **Target correctness.** Exercise stale references, duplicate labels, disabled
   controls, clipped or hidden content, dynamic View IDs, Compose semantics, and
   dialogs/popups. Confirm that missing resource names do not break observation
   of programmatically created controls. Compare with Android before claiming
   physical hit testing or window ownership fidelity.
3. **Lifecycle safety.** Inspect PID reuse, process exit races, symlinked instance
   paths, cleanup ownership, and the Linux/macOS signaling differences. The legacy
   fleet still has mutable build paths and no global scheduler or exclusive leases.
4. **Transport and packaging.** Run the real installed MCP subprocess test, verify
   its dependency versions, and inspect cancellation and large-message behavior.
   The in-process suite uses a test-only timer for this sandbox's worker wakeup
   restriction; it is not equivalent to a working installed transport.
   `tests/mcp_sandbox_diagnostic.py` isolates that restriction with a timer-assisted
   installed server. Its success never substitutes for `tests/mcp_stdio.py`, which
   also verifies UTF-8 under ASCII output settings, oversized input, and stdin EOF.
5. **Runtime changes.** Compile and run both fixtures before accepting Kotlin
   changes. Add a regression for each confirmed defect. Activity restoration must
   continue to state that application statics and singletons survive.

## Acceptance and next milestone

For each finding, provide severity, file/line, trigger, observed behavior, expected
behavior, and a reproduction or explicit unverified hypothesis. Keep fixes small
and update the PR and validation record with actual results.

Each milestone needs an evidence report under [evidence/](evidence/) answering the
roadmap's product review questions. Keep follow-up PRs in draft while a gate is
unresolved. Ensure installation instructions match the branch being evaluated, and
retain the experimental status until the documented validation gates pass.
