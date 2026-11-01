# NoEmu implementation roadmap

The product goal is reproducible Android experiments for coding agents: independent
app environments, controlled conditions, explicit assertions, replayable evidence,
and optional checks against real Android. This roadmap replaces the historical
product conclusions and fleet plans. Historical reports remain as evidence of
specific earlier experiments.

## Current gate: A — repair the foundation

The current change addresses unsafe instance paths and PID-only signaling,
misreported command failures, multiline transport, malformed MCP requests,
ambiguous/stale/disabled targets, horizontal scrolling, benchmark false positives,
and misleading process-death terminology. It adds maintained Views/Compose fixtures,
versioned JSON, bounded messages, and official MCP SDK handling.

**Exit gate:** Python regressions, driver integration tests on both fixtures, and
clean package-install tests pass. Known failures must not be reported as success.
Until the driver and packaging gates run successfully, A is incomplete and the
new runtime behavior remains unverified. See [VALIDATION.md](VALIDATION.md).

## B — useful fleet and first external pilot

Implement one local supervisor per OS user, shared across projects and worktrees:
private Unix socket, transactional SQLite metadata, immutable runtime generations,
verified process identity, exclusive leases, queues, readiness/recovery, resource
admission, bounded cleanup, and retention. Keep Python orchestration and one Kotlin
app JVM per instance. Serialize each instance's actions and run independent
instances concurrently.

Initial configurable defaults: two active instances, four total JVMs, one cold
start, and one warm instance. Memory admission uses the smaller of 4 GiB and 40%
of available machine/container memory; sleeping JVMs still count. Released instances
are destroyed, and replacements start clean.

Snapshot classpaths and Android resource inputs, rewrite generated references, and
publish generations atomically. Existing instances retain their generation when
later builds succeed or fail. Add `acquire`, `release`, `sleep`, `wake`, `status`,
`gc`, JSON output, and MCP equivalents, preserving `spawn`, `destroy`, and `NOEMU_ID`.

**Exit gate:** concurrent agents across worktrees cannot share an exclusive instance,
exceed configured limits, cross instance data boundaries through NoEmu, or run a
different generation accidentally. This is the first external pilot release.

## C — Android conformance laboratory

Define independent expected outcomes and run the same scenarios on the JVM and
instrumented Android fixtures. Cover initialization order; activity launch/back and
supported launch modes; rotation/saved state; disabled, hidden, overlapping and
ambiguous targets; dialog/popup ownership; permissions; service binding; clocks and
scheduled work; persistent data; and network disconnect/reconnect.

Normalize only explicitly irrelevant differences. Preserve meaningful text,
quantities, state, ordering, and callbacks. Reduce mismatches and classify them as
NoEmu defects, Robolectric defects/unsupported behavior, Android-version differences,
fixture defects, or unresolved discrepancies.

**Exit gate:** a versioned, repeatable compatibility report exposes every failure
and incomplete case instead of skipping or relabeling it as a pass.

## D — evidence-based runtime improvement

Review provider, service, activity, and clock adapters against C. Narrow and version
compatibility behavior, prefer supported APIs, and require a fixture for every
workaround. Prepare upstream-ready Robolectric fixes when appropriate; submitting
them requires a separate explicit instruction. Local patches need an upstream
issue reference and removal condition.

Investigate upstream Robolectric Simulator's existing headless and remote-control
hooks behind `prepare → launch → observe → act → collect-artifacts → terminate`.
Check release availability. Adopt only after it passes all supported fixtures,
capability requirements, and published performance gates; otherwise record blockers
and retain the current driver.

**Exit gate:** every claimed improvement has before/after evidence and an explicit
Android/runtime version scope.

## E — replayable and comparable sessions

Add versioned JSON flows and schema, `record`, `replay`, preparation-recipe
`checkpoint`, generation `compare`, `batch`, `wait-for`, and `assert`. Include named
inputs and secret references; do not persist secret values by default. A checkpoint
recreates a verified state from a clean instance; it is not a JVM snapshot.

Use one run record for JSON, JUnit XML, and standalone HTML. Include build/profile
identity, assertions, first divergent action, semantic differences, optional images,
crashes, logs, timing, replay instructions, and nondeterministic dependencies.

**Exit gate:** a manual success becomes a replayable regression check that
reliably distinguishes a known buggy revision from its fix.

## F — optional Android verification

Use an explicitly selected ADB target and pinned Maestro adapter for the supported
flow subset. Verify app/build identity, capabilities, selectors, equivalent
assertions, and the explicit clean-state policy. Do not provision devices, boot
emulators, guess coordinates, or silently clear data. Preserve JVM-only use.
Deep lifecycle conformance uses instrumentation; Maestro serves user-facing flows.

**Exit gate:** selected bug/fix pairs reproduce on both backends, and a deliberately
inaccurate JVM behavior is reported as a discrepancy with both traces.

## G — bounded experiment engine

One specification selects generations, preparation, journey/assertions, a bounded
profile/fault matrix, and resource/time budgets. Initial capability-gated scenarios:
rotation, supported network interruption, background/resume, locale, theme, and
screen size. Schedule through B, group equivalent failures, and minimize action
sequences by rerunning candidates. Accept reductions only if the same failure
signature and assertion reproduce. Existing agents provide candidate code changes.

**Exit gate:** one experiment compares several revisions and conditions and gives
a concise, reproducible account of failed assertions and their evidence.

## H — bounded process-restart research

Prototype a fresh JVM with explicitly preserved app storage. Distinguish cold
relaunch, activity state restoration, and Android task restoration after process
death. Prove that statics and singletons reset. Report unsupported Binder handles,
file descriptors, saved values, and incomplete task restoration explicitly.

**Exit gate:** publish differential results and supported semantics. Ship only
what passes; retain the name `cold restart` if task restoration is still inaccurate.

## Evidence and performance gates

On documented 4-vCPU/16-GiB hardware, target p95 ≤1 second for warm acquisition,
wake to usable observation, simple semantic actions, and confirmed process
destruction. Measure cleanup separately. Use at least 100 warm measurements and
separate build time, JVM start, pooled acquisition, and screenshots. Run 1,000
lifecycle cycles with no orphan processes or cross-instance data leakage.

Track false verification, incomplete runs, replay coverage, runtime patch tests,
backend versions/capabilities, memory, throughput, model cost, and setup failures.
Require three external developers completing real workflows and evidence of repeat
use. Compare with a capped emulator workflow on KVM-capable hardware and separately
demonstrate utility without KVM. Targets are not measured achievements.

Every work unit follows: inspect → define acceptance → reproduce/baseline → smallest
complete change → relevant checks → evidence and limitations. Advance only when the
current milestone's exit gate passes. No wholesale runtime rewrite, permanent fork,
cloud service, or arbitrary JVM snapshots are part of the initial implementation.

## Product review at each milestone

Before closing a milestone, answer these questions in its evidence report:

1. Which real developer or agent workflow becomes easier, and what can the user
   now accomplish that was previously unreliable or impractical?
2. What advantage does the complete NoEmu workflow demonstrate over a direct
   Robolectric session or a capped emulator workflow? Separate measured advantages
   from hypotheses; speed alone is insufficient when verification is inaccurate.
3. What reproducible evidence supports the claim, including negative cases,
   unsupported behavior, resource cost, and incomplete runs?
4. What is the smallest next improvement that addresses a demonstrated user need?
   Keep it within the ordered roadmap and the current milestone's exit gate.

Use these answers to prioritize work autonomously. A distinctive feature earns
its place by improving a workflow and passing its checks, not by expanding the
feature list or strengthening the wording of an unverified claim.
