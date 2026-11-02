# Plan: milestone C — Android conformance laboratory

Status: proposed, 2026-11-02. ROADMAP.md remains the authority; this note records how
to start.

## Why C is next

Milestone B makes concurrent, isolated JVM experiments dependable, and
`examples/rotation-fixes` tells candidate fixes apart. Every verdict is still a JVM
simulation. The most important remaining limitation is that nothing shows Android
agrees. C measures that, scenario by scenario, and keeps every disagreement visible.

## Design

1. **Scenario specifications** (`conformance/scenarios/*.json`, versioned): a fixture
   activity, a sequence of semantic actions, and expected observations written from
   Android documentation and behavior, not from NoEmu output. Each scenario names its
   source for the expectation.
2. **JVM backend**: runs each scenario on a leased instance through the existing
   protocol and records results with generation identity.
3. **Android backend**: the same scenarios as instrumented tests (`androidTest`
   with Espresso or UI Automator) on an **explicitly selected** ADB serial. No device
   provisioning, emulator boot, or data clearing by NoEmu; the run records the device
   model, API level, and build fingerprint.
4. **Report** (`conformance/report-<date>.json` plus Markdown): per scenario and
   backend, `matched`, `mismatch`, `unsupported`, or `incomplete`. Each mismatch is
   classified as a NoEmu defect, Robolectric defect or unsupported behavior,
   Android-version difference, fixture defect, or unresolved. Only explicitly
   irrelevant differences are normalized, and each normalization is listed.

## First scenarios

Start with behavior the fixtures already exercise, so mismatches are cheap to
investigate:

- Saved versus lost field text across rotation (Views and Compose).
- `CounterActivity` variants across rotation and one more action.
- Window focus after launch and after recreation (a known JVM gap).
- Disabled, hidden, and duplicate targets.
- Back navigation from a second activity.
- Horizontal scroll changing visible items.
- Storage visible after activity restoration (`restore-activity`).

## Prerequisites and open decisions

- **An Android target.** A Pixel 8a AVD (API 36) exists on the development PC; no
  physical device was attached. Booting and driving it for hours is the machine
  owner's decision, so this session did not start it.
- Instrumented tests need the fixtures' `androidTest` source sets and AndroidX Test
  dependencies, pinned like the existing ones.
- The JVM runtime is API 36 (Robolectric 4.16). Compare against API 36 first, and
  treat other API levels as Android-version differences.

## Exit gate

A versioned, repeatable report covers every scenario on both backends, and every
failure and incomplete case is visible: none is skipped or relabeled as a pass.
Running it twice on the same revisions and device gives the same classifications.
