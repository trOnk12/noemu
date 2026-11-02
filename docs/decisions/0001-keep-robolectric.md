# 1. Build on Robolectric; do not port the Android framework

Status: accepted, 2026-11-02.

## Context

NoEmu runs app code on the JVM. Two directions were considered: keep Robolectric as
the runtime and fix its gaps, or port the Android framework code NoEmu needs into
its own runtime.

## Decision

Keep Robolectric. NoEmu's product is the layer above the runtime: isolated revisions,
controlled conditions, replayable assertions, and evidence that separates JVM
simulation from behavior verified on Android.

## Reasons

- Robolectric runs real Android framework code (android-all jars) under shadows. A
  port would rebuild that while giving up the community that maintains it.
- Every defect found so far was narrow and fixable from outside: one missing frame in
  `ActivityController.recreate()`, and Compose layout that waits for a draw. Neither
  called for a different runtime.
- The roadmap excludes a wholesale runtime rewrite and a permanent fork. Local
  workarounds such as `RecreationFrame` carry a removal condition and become
  upstream-ready fixes in milestone D.
- Robolectric's upstream Simulator already has headless and remote-control hooks.
  Milestone D evaluates it as a backend against the same gates.

## Consequences

- Fidelity claims stay bounded by Robolectric's version and NoEmu's conformance
  corpus (milestone C). Where the JVM diverges from Android, the report shows it.
- NoEmu's value must come from what it adds, not from a claim of better fidelity.
- Reconsider only if milestone C shows systematic gaps that shadows cannot close.
