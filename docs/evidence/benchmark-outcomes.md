# Benchmark outcome integrity

Review after `32f9d2e` found remaining milestone A defects in the historical
benchmark harnesses. Two regressions failed before the fix: a study command with
nonzero exit status returned error text as UI evidence, and successful command
stderr was mixed into its observation. That can satisfy a text assertion even
though the requested UI action failed.

The study now checks command status before evaluating stdout. Version 2 score
records retain operation and cleanup failures separately. Timeouts and launch
errors are inconclusive; unknown tasks are unsupported. A successful assertion
with unsuccessful cleanup does not become an overall pass. Later tasks still run.

The corpus runner checks clone, revision discovery, startup, crawl and cleanup.
It retains partial timeout output and existing workspaces, stops rewriting Gradle
versions, and records the actual checkout revision. A passing record describes
command execution only. It is not a declaration that an app has no bugs.

Both runners use the installed Python module and remove inherited `NOEMU_ID` so
they cannot accidentally direct benchmark actions at a shell-selected instance.
The probe script preserves Gradle's failure exit even when diagnostic lines exist.
Scoring and corpus commands exit nonzero for failed or incomplete checks.

Regression tests inject subprocess outcomes and execute the real probe shell
script against a fake Gradle wrapper. They do not execute Android apps or a coding
model. Historical study scores and corpus findings remain unchanged and unverified
by this repair. Text assertions remain heuristics, not the semantic replay or
Android verification planned in later milestones.

On 2026-11-02, the complete Python suite passed **107 tests in 29.784 seconds**.
The benchmark subset passed 20 tests, including 18 added regressions. Both original
failure cases were reproduced before the repair. Probe Bash syntax and Git
whitespace checks passed. No paid model, corpus app, or Android run was performed.

Full milestone A validation on the provisioned Android toolchain remains pending.
