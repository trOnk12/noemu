# Review and continuation guide

This guide is for maintainers, external reviewers, and coding agents reviewing
[draft PR #1](https://github.com/trOnk12/noemu/pull/1). The repository and PR are
public. Describe implemented behavior separately from verified behavior, and keep
unfinished checks visible.

## Scope and current state

The PR implements part of milestone A: process and instance safety, versioned JSON
transport, truthful CLI/MCP failures, structured observations, UI targeting changes,
maintained fixtures, and package/driver CI definitions. It does not complete A or
implement the supervisor, replay, Android verification, or experiment engine.

Read [VALIDATION.md](VALIDATION.md), [PROTOCOL.md](PROTOCOL.md), and
[ROADMAP.md](ROADMAP.md) before extending the implementation. The latest recorded
local suite has 63 passing Python tests. Wheel smoke checks reused dependencies.
Installed MCP stdio initialization timed out. Kotlin and live-driver validation
have not passed. GitHub Actions jobs were blocked before execution by an account
billing issue; this is not evidence for or against the code's runtime correctness.

Work from a normal clone and inspect the current remote branch before editing:

```sh
git clone --branch codex/noemu-foundation https://github.com/trOnk12/noemu.git
cd noemu
git status --short --branch
git log -5 --oneline
```

The branch already contains the published work. Do not apply an old conversation
patch bundle again or replace its history with an exported source snapshot.

## Checks to run

Use Python 3.10+ and a virtual environment. From the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e . build
python -m unittest discover -s tests -t . -v
python -m build
```

Install the wheel and its dependencies into a fresh virtual environment without
`--system-site-packages`. Run its CLI and MCP integration outside the source
checkout so local imports cannot conceal missing package files:

```sh
repo="$PWD"
check_dir=$(mktemp -d)
python -m venv "$check_dir/venv"
"$check_dir/venv/bin/python" -m pip install "$repo"/dist/*.whl
cd "$check_dir"
"$check_dir/venv/bin/noemu" --help
"$check_dir/venv/bin/python" "$repo/tests/mcp_stdio.py"
cd "$repo"
```

For Kotlin and live-driver checks, provision JDK 21, SDK platform 35, build tools
35.0.0, and Gradle 8.11.1. Follow [fixtures/README.md](../fixtures/README.md), running
both `NOEMU_FIXTURE=views` and `NOEMU_FIXTURE=compose`. The driver uses Robolectric
4.16 / API 36; these are JVM tests, not instrumented Android conformance tests.
The workflow in [test.yml](../.github/workflows/test.yml) specifies the CI matrix.

Record the tested commit, runtime versions, command, exit status, and failure log.
Do not turn unavailable prerequisites or cancelled jobs into skips or passes.

## Review priorities

1. **Truthful outcomes.** Confirm that driver failures, background crashes, partial
   execution, malformed replies, and timeouts cannot become successful CLI/MCP
   results. Check the driver parser as well as Python validation.
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
5. **Runtime changes.** Compile and run both fixtures before accepting Kotlin
   changes. Add a regression for each confirmed defect. Activity restoration must
   continue to state that application statics and singletons survive.

## Acceptance and next milestone

For each finding, provide severity, file/line, trigger, observed behavior, expected
behavior, and a reproduction or explicit unverified hypothesis. Keep fixes small
and update the PR and validation record with actual results.

Milestone A requires passing Python, both driver fixtures, and clean package-install
checks, with truthful failure paths. Advance to B only when that gate passes.
Keep the PR in draft while these checks are unresolved. Before merging, update
the README's draft/branch installation instructions to match the release state.
Publishing fixes to the review branch does not require merging or releasing them.
