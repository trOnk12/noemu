# Gate revision and evidence guarantees

This follow-up fixes the validation harness reviewed at
`8fd9a8073efad1c823d23f43779d0da338c0e376`. It does not change the Android driver
and does not claim a new full runtime pass.

## Reproduced failures

Five new regressions failed against the previous gate:

- A nonexistent requested revision could return exit 0 and `gate PASSED`.
- A failed checkout could continue testing the clone's default revision.
- Failed virtual-environment creation did not stop preparation.
- Successful runs deleted their nested JUnit reports.
- Failed driver runs deleted JVM logs referenced by their error messages.

The tests invoke the real shell gate in disposable Git repositories. Git operations
are real; package installation, Gradle, and Android test programs are explicit stubs
used to inject outcomes. These prove gate control flow, not Android behavior.

## Implemented guarantees

- Resolve a verified commit and stop on any preparation error.
- Verify the clone's actual HEAD before running checks.
- Continue independent checks after a test failure, retaining a nonzero result.
- Reject reuse of nonempty result directories.
- Record `incomplete` until all checks and evidence collection finish.
- Retain selected fixture logs and JUnit XML immediately after each check, before
  another Gradle invocation can overwrite them. Record byte counts and SHA-256 hashes.
- Refuse symlink evidence; cap each collection at 500 files, 2 MiB per file and
  16 MiB total. Truncation or collection errors prevent a passing gate.
- Remove the temporary checkout on success; retain it on failure or interruption
  and report its location for inspection and later manual cleanup.
- Run live driver checks with the installed wheel. Reject an installed import from
  outside its virtual environment and remove inherited Python import-path overrides.

## Measured validation

On 2026-11-02, `python -m unittest discover -s tests -t . -q` passed **82 tests in
8.860 seconds**, including eight gate control-flow tests and four artifact tests.
The first five regressions failed before the fix and passed afterward. Bash syntax
and Git whitespace checks passed.

The prior three full Linux runtime runs at `62efbba` remain recorded in
[milestone-a.md](milestone-a.md). This follow-up environment has no Gradle or Android
SDK, so the full gate with these changes has **not** run. Execute
`scripts/gate.sh <follow-up-commit> <empty-results-dir>` on the provisioned host and
retain the results bundle before advancing to milestone B.

## Command deadlines

A subsequent regression stalled the Views check for 30 seconds; the previous gate
exceeded the test harness's 15-second deadline instead of recording a failed check.
The gate now bounds preparation, environment discovery, checks and artifact capture.
A timed-out check records exit 124, retains evidence, and permits independent checks
to continue. Cancellation stops the active command group and leaves `incomplete`.

Real Linux child-process tests cover success/failure propagation, invalid budgets,
missing executables, timeout and cancellation. A descendant ignores TERM while its
parent exits; the runner still kills that descendant before returning. Shell gate
tests cover timeout evidence/continuation and cancellation forwarding. They are
validation-harness evidence, not Android execution or macOS evidence.

On 2026-11-02, the complete Python suite passed **89 tests in 33.327 seconds**,
including seven added deadline/cancellation tests. Bash syntax and Git whitespace
checks also passed. The full Android-toolchain gate remains pending at this revision.

Deadlines do not bound the whole gate or guarantee termination of detached processes
and uninterruptible kernel work. See [configuration and limits](../REVIEW.md#checks-to-run).
Failure workspaces are retained separately from the bounded copied artifacts and
must be removed explicitly once their diagnostics are no longer needed.
