# noemu next to the usual tools

The same apps, the same bugs, measured in the same cloud container (4 CPUs, no KVM). Where a number was not measured
here, the row says where it comes from.

## Can it run where agents and CI run?

| | Result in this container |
| --- | --- |
| Android Emulator (`emulator -accel-check`, SDK emulator package installed for the test) | `KVM requires a CPU that supports vmx or svm`, exit 3. Current x86_64 system images don't boot without it, so tools built on an emulator or a device (adb, UI Automator, Maestro, Appium, the Android MCP servers) have nothing to drive. |
| noemu | Starts the app in 25-90 s on a warm Gradle cache (Jetsnack 25 s, Jetchat 37 s, Fossify Calendar 76 s), 5-7 min on the first build of a large app (Now in Android, Read You). |

## Does the standard static check catch these bugs?

Android Lint (`./gradlew :app:lintDebug`, AGP's default checks) on the unmodified apps where noemu found bugs:

| App | Lint warnings | On the buggy files | noemu |
| --- | --- | --- | --- |
| Jetchat | 123 (43 unused resources, 35 typos, 13 newer versions, ...) | 0 | crash: same message sent twice (duplicate `LazyColumn` key) |
| Jetsnack | 43 (13 Gradle dependency versions, ...) | 0 | search query lost on rotation; "see more" reset |

These bugs are in values at run time (an id built from a fixed string, state kept with `remember`). A static check
sees code, not those values.

## What an agent pays to look at a screen

Per look at the same screen, as an agent receives it:

| Screen | noemu `screen` (text, numbered targets) | Screenshot (1233x2673, resized by the API to 723x1568) |
| --- | --- | --- |
| Jetchat conversation | 1,215 characters, 23 elements, about 350 tokens | about 1,510 tokens |
| Jetsnack home | 1,496 characters, 31 elements, about 430 tokens | about 1,510 tokens |
| Jetsnack search | 859 characters, 20 elements, about 250 tokens | about 1,510 tokens |

Text tokens are estimated at 3.5 characters a token; image tokens use Anthropic's published formula (width x height /
750, after the resize). The text names every target (`tap 16` or `tap "Sign in"`), where a screenshot leaves the agent
to estimate coordinates. Element lists from device tools (for example mobile-mcp's) are of a similar size to noemu's,
but need the device.

Per task, noemu did not make an agent cheaper: in the [agent study](AGENT_STUDY.md) the agent with noemu spent about
15% more ($1.04 against $0.91 for six tasks), because it ran the app to check its work.

## What each kind of tool finds

| | Crashes | State lost on rotation | State lost after the app is killed | Views stacked on rotation | Leaked screens | Needs |
| --- | --- | --- | --- | --- | --- | --- |
| noemu crawl | yes | yes, checked on every screen | yes | yes | yes, with the chain that holds the screen | Gradle project, JDK |
| Android Lint | no | no | no | no | no | Gradle project |
| Monkey, Firebase Robo, Maestro flows | yes | not part of their reports | not part of their reports | no | no | emulator or device (Robo: cloud devices) |
| LeakCanary | no | no | no | no | yes, on a device in debug builds | device, a dependency in the app |
| Research tools (RotDroid, iFixDataloss, DDLDroid) | some | yes | some | no | no | emulator, instrumentation or a fine-tuned vision model |

## Leaks: what noemu reports

After a rotation, a background kill or a closed screen, noemu checks that the destroyed activity can be collected.
When one can't, it dumps the heap and runs [Shark](https://square.github.io/leakcanary/shark/), LeakCanary's analyzer,
for the chain that holds it. Chains that only pass through Robolectric's own bookkeeping are listed as JVM limits.

| App | Crawl (100 actions) |
| --- | --- |
| Fossify Calculator with a listener left on an app-wide object (6 lines added) | leak: "MainActivity still in memory after rotating, held by: `ThemeChanges.listeners` → `ArrayList[0]` → `MainActivity$$Lambda.arg$1` → `MainActivity`" |
| Fossify Calculator, Notes; Jetsnack, unmodified | no leaks |
