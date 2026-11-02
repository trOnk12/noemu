# NoEmu

**An Android experimentation platform for coding agents.**

NoEmu runs Android application code on the JVM with Robolectric and exposes it
through a Python CLI and an MCP server. Agents can inspect Views and Compose
semantics, interact with an app, exercise lifecycle changes, and collect evidence
without booting an emulator or requiring KVM.

The goal is to make Android behavior as easy to explore, reproduce, and compare
as source code. A successful JVM operation establishes what happened in that
simulation. Verification on Android is a separate, planned capability.

> **Experimental.** Milestone A's runtime gate passed on Linux (WSL2): Python
> regressions, a clean wheel installation with the MCP stdio check, Kotlin compilation,
> and live driver tests on both fixtures. This shows that the driver does what its
> tests assert on the JVM. It does not show that behavior matches Android; that
> comparison is milestone C. macOS has not been validated. The shared supervisor,
> immutable builds, replay engine, and Android verification adapter are planned. Read
> the [validation record](docs/VALIDATION.md) before relying on verification results.
> Those full runs tested `62efbba`; the subsequently hardened gate still needs its
> full Android-toolchain rerun.

## Implemented interfaces

The following interfaces exist in this branch. Their presence does not establish
Android compatibility; the fixture and platform checks are tracked separately.

- Launch an Android source project on the JVM using its Gradle build.
- Inspect visible elements from Views and Compose semantics.
- Tap a unique label or an observed element; enter text, including multiple lines.
- Navigate back, open activities or app links, rotate, and restore activity state.
- Inspect screenshots and logs; run the experimental lifecycle crawler.
- Build once and start separate JVM instances with the existing fleet commands.
- Drive the same commands through the official Python MCP SDK.

The Android target of a Kotlin Multiplatform project can be used when its app
module applies `com.android.application`. Other KMP targets are outside scope.
Linux and macOS are the intended hosts; only Linux has run the full gate. Native
Windows is not a supported host. Validation coverage is recorded separately from
platform support goals.

## Requirements

- Python 3.10 or newer.
- JDK 21 for the driver, which currently uses Robolectric 4.16 and Android API 36.
- The Android SDK and Gradle wrapper required by your app's source project.
- Access to the project's Maven and Gradle dependencies for the initial build.

An APK alone is insufficient: NoEmu runs the app's compiled JVM code and resources.
Android native libraries require compatible host libraries. Google Play services,
hardware, graphics, and operating-system behavior may be unavailable or differ.

## Install

The foundation implementation is available on `main`. It is experimental; a merge
does not establish that the Android validation gates have passed. Install from
source to evaluate the current development version:

```sh
pipx install 'git+https://github.com/trOnk12/noemu.git@main'
```

For development, install the checked-out revision:

```sh
git clone https://github.com/trOnk12/noemu.git
cd noemu
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The CLI, Kotlin driver, Gradle init script, and runtime service registrations ship
in the Python package. After updating NoEmu, rebuild and relaunch existing app
instances: the versioned JSON transport is incompatible with the old text driver.

## Quick start

From the Android project root:

```sh
noemu doctor --module :app
noemu start --module :app
noemu screen
noemu tap "Sign in"
noemu type "Email" "agent@example.com"
noemu rotate 2
noemu shot /tmp/noemu-screen.png
noemu logs
noemu stop
```

For product flavors, supply the actual unit test task, for example
`--task testDemoDebugUnitTest`. Use `--activity com.example.EditorActivity` to
select an activity and `--data URI` to supply intent data.

The init script adds NoEmu's test sources and dependencies to the selected module
for that Gradle invocation. Gradle still writes its normal build outputs.

### Targeting and failures

Labels must match uniquely, ignoring case. Duplicate matches fail. Disabled
controls fail before an action is invoked. Numeric indices refer to the last
observation and are checked against the current element. The JSON driver protocol
also exposes observation-scoped references, full labels, state, bounds, and
available resource-ID or Compose test-tag selectors.

Request a fresh screen after the UI changes. A stale reference is an error; NoEmu
does not translate a failed selector into guessed coordinates. Semantic actions
are not a substitute for Android hit testing or accessibility conformance.

Commands that fail return a nonzero CLI status or an MCP `isError` result. A
timeout means the outcome may be partial: inspect the instance before retrying a
mutation. See [the transport contract](docs/PROTOCOL.md).

### Activity state and network controls

```sh
noemu restore-activity
noemu network off
noemu network on
noemu network slow 300
```

`restore-activity` rebuilds the activity from parcelled saved state **inside the
same JVM**. Application statics and singletons survive. The legacy `kill` command
is an alias for this operation; it does not simulate genuine process death or
Android task restoration.

Network controls operate through the JVM proxy and simulated connectivity state.
Clients that bypass the proxy, including some raw-socket, NIO, and UDP clients,
are not covered. Permissions are currently granted automatically; permission
denial experiments are not supported.

## Experimental fleet

```sh
noemu build --module :app
noemu pool 1
noemu spawn                       # prints: ID PORT
NOEMU_ID=0 noemu screen            # use the returned ID
noemu --id 0 tap "Sign in"
noemu sleep 0
noemu wake 0
noemu destroy 0
noemu list
```

`spawn --no-refill` disables background pool replenishment. `destroy all` destroys
verified live instances in the current project's fleet. Destroy waits for process
exit before removing that instance's allocated temporary directory. Lifecycle
commands require recorded PID, creation time, and owner identity. Old directories
containing only a PID are deliberately not trusted for signaling.

The current fleet stores metadata under the project’s `.gradle/` directory. It
has **no shared resource scheduler or immutable build snapshot**. Do not treat it
as the isolation and cross-worktree lease system described in milestone B.
Sleeping JVMs retain memory. A changed build can still invalidate paths used by
older instances; concurrent revision comparison must wait for immutable generations.
Memory output is resident memory in MiB for the verified process. An unavailable
measurement is shown as `memory unknown`, never as zero usage.

## MCP integration

Launch `noemu mcp` with the Android project root as its working directory. A
client that supports a working-directory field can use:

```json
{
  "mcpServers": {
    "noemu": {
      "command": "noemu",
      "args": ["mcp"],
      "cwd": "/absolute/path/to/android-project"
    }
  }
}
```

Working-directory configuration varies by client. The server uses stdio and the
official MCP SDK. Tool inputs are schema-validated; malformed messages do not
terminate the session. Tool calls are serialized while the legacy orchestration
uses process-global instance selection.

Driver operations return the same versioned result as `structuredContent`, alongside
readable text (or an image for `shot`). Agents can inspect observation references,
selectors, identity, timing, artifacts, and limitations without parsing console text.
Failed operations retain their structured result and set `isError`.
`scroll` accepts a `target`; `rotate` accepts `count`; `crawl` accepts `steps` and
`minutes`. `restore-activity` preserves application statics and singletons.
For `network`, `delay_ms` is required with `state: "slow"` and rejected otherwise.

## Configuration

| Variable | Purpose |
| --- | --- |
| `NOEMU_ID` | Select a fleet instance; `--id` takes precedence. |
| `NOEMU_SHOTS=0` | Disable automatic screenshots; explicit `shot` still works. |
| `NOEMU_SETTLE_MS` | Limit waiting for the UI to settle; default 4000 ms. |
| `NOEMU_XMX` | Heap size for a fleet JVM; default `1g`. |
| `NOEMU_JVM_ARGS` | Additional fleet JVM options. |
| `NOEMU_IDLE_MIN` | End a fleet instance after its idle interval. |
| `NOEMU_GRADLE_ARGS` | Additional arguments for the Gradle invocation. |
| `NOEMU_DEPS` | Comma-separated test-runtime dependencies, such as host-native alternatives. |

## Development and verification

Start with the [review and continuation guide](docs/REVIEW.md) for the current
scope, exact checks, and open review questions.

```sh
scripts/bootstrap-toolchain.sh   # JDK 21, Gradle 8.11.1, Android SDK, in ~/tools/noemu
. ~/tools/noemu/env.sh
scripts/gate.sh                  # every check, from a fresh clone of HEAD
```

The [Views and Compose fixtures](fixtures/README.md) contain both correct and
intentionally broken behaviors. Their integration runner requires the Android
build toolchain and fails when prerequisites are missing. `scripts/gate.sh` runs the
Python suite, a clean wheel installation, Kotlin protocol checks, and live driver
checks for both fixtures. CI defines the same checks, but a configured job is not
evidence of a passing run.

Historical experiments live in [bench/](bench/). Their timings and findings apply
to those recorded revisions and conditions. They do not establish current p95
performance, Android conformance, or the roadmap's 1,000-cycle reliability target.

## Architecture and direction

Python owns orchestration; Kotlin executes the app on Robolectric. The planned
supervisor will manage immutable builds, exclusive leases, resource budgets, and
experiment records. Replays will carry explicit assertions, capabilities,
limitations, and backend identity. Optional Android verification will replay a
supported subset against an explicitly chosen ADB target through Maestro.

Robolectric's upstream Simulator also has headless execution and remote-control
hooks. NoEmu will evaluate it against the same corpus before considering a backend
change; release availability and compatibility still require verification. The
product's value is the complete experiment and evidence workflow.

Contributions should include a minimal reproduction, an executable acceptance
check, and the runtime/Android scope of any compatibility claim. Keep changes small;
advance the roadmap only after the preceding exit gate passes.

## License

[MIT](LICENSE).
