# Review and continuation guide

This guide is for maintainers and coding agents continuing the work. Describe
implemented behavior separately from verified behavior, and keep unfinished checks
visible. A merged PR does not establish a passing validation gate.

## Scope and current state

Milestone A's automated gate passes; see [its evidence](evidence/milestone-a.md).
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
and the live driver tests on both fixtures. Every check runs even after a failure.
Each writes a log to the results directory; the exit status is nonzero if any failed.

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
