# noemu

**Run your Android app and drive it, with no emulator.** For coding agents in sandboxes that can't run one, and for
anyone who wants to see a screen in seconds instead of booting a device. `noemu crawl` then taps through the app on its
own and reports crashes and what a rotation loses.

![noemu finding bugs in Google's Compose samples and Fossify apps](docs/demo.gif)

```text
$ noemu start
noemu: app running (31s)

activity MainActivity
[2] button Filters  @108,252  (tap)
[10] button Cupcake A tag line  @327,879  (tap)
[16] button Chips  @252,1721  (tap)
[27] button HOME  @246,2589  (tap)
[29] button MY CART  @861,2589  (tap)
screenshot .gradle/noemu/screen.png

$ noemu tap "MY CART"
tapped "MY CART"
activity MainActivity
[1] text Order (3 items)  @303,252
[3] button Gingerbread A tag line $4.99 Qty 2  @616,535  (tap)
[9] text Subtotal  @168,1729
[10] text $54.44  @1085,1729
...
```

That is Google's Jetsnack sample, unmodified, running in a cloud container with no KVM.

## How

Your app runs on the JVM under [Robolectric](https://robolectric.org/), inside a test that noemu's Gradle init script
adds to the app module **for that build only**: nothing in your project changes, nothing to commit. The app starts the
way Android starts it (content providers, then `Application.onCreate`, then the launcher activity), with your real
dependency graph (Hilt, Room, WorkManager, DataStore), and stays running while you send it commands.

`screen` lists what's on screen, numbered, from the Compose semantics tree and the View hierarchy (the same things
accessibility services and UI tests read), in the window that takes the touches: an open dialog or menu, else the
activity. Each command returns the new screen and saves a screenshot.

## Crawl for crashes and lost state

`noemu crawl 100` taps through the app on its own, typing into fields and submitting them. On every new screen, and
after every action that changes one, it rotates the device there and back: the activity is rebuilt from its saved
state, with the other orientation's layouts on the way. Once per screen it also kills the app in the background and
reopens it, as Android does to apps you leave: ViewModels gone, only saved state left. It reports:

- **problems**: crashes (where thrown, and the taps that led there), text it typed that a rotation lost, views
  stacked again on each rotation, and screens leaked in memory, with the chain of references that holds them (found
  by LeakCanary's analyzer);
- **worth a look**: anything else a rotation changed, such as a search bar closing, which may be fine.

```text
$ noemu crawl 100        # Fossify Calendar, the release before its fix for issue #644
crawled 30 actions over 30 distinct screens; 0 distinct crash(es), 1 rotation problem(s)

MainActivity: 52 elements before rotating there and back, 144 after; shown more than once now: "button Go to
previous month", "button October", "button Go to next month"
  after: rotate > rotate
```

Users reported that one as [#644](https://github.com/FossifyOrg/Calendar/issues/644), "App crashes after rotating
several times": each rotation stacked another copy of the calendar until the app died (the crawl slowed down too, and
stopped at its 10-minute budget). The release with the fix shows no stacking. Failures of the JVM stand-in itself (a
GPU-only shader, say) are listed apart, as not app bugs.

[What the crawl found on real apps](bench/CRAWL_EVIDENCE.md): in Google's own Compose samples, a crash in Jetchat
(send the same message twice) and a search query that Jetsnack, Reply and Jetcaster each lose on rotation; five
historical bugs from Fossify Calendar, File Manager, Calculator and Notes, none reported on the fixed code; no crashes on
today's Fossify apps.

## Install

```bash
pipx install git+https://github.com/trOnk12/noemu
```

It needs Python 3.10+, JDK 17+ and what your project already builds with (Android SDK, Gradle wrapper). Robolectric
comes in through Gradle, for the run only.

## Use

```bash
cd your-android-project
noemu start [--module :app] [--task testDebugUnitTest] [--activity com.example.EditorActivity [--data file:///tmp/a.txt]]
noemu screen
noemu tap 16              # by number
noemu tap "Sign in"       # or by label
noemu type 2 "hello@example.com"
noemu scroll down | noemu back | noemu rotate | noemu kill | noemu leaks | noemu wait 2000
noemu shot out.png | noemu logs
noemu crawl 100 [MINUTES]   # every decision it takes goes to .gradle/noemu/crawl.log
noemu stop
```

## Tested on

| App | Stack | Start |
| --- | --- | --- |
| Jetsnack (android/compose-samples) | Compose | 31 s |
| JetNews (android/compose-samples) | Compose | 75 s first build |
| JetLagged (android/compose-samples) | Compose, AGSL shaders (`RuntimeShader`) | 13 s |
| Now in Android | Compose, Hilt, Room, WorkManager, Firebase, dialogs, Gradle isolated projects | 93 s first build |
| Fossify Notes | Views (XML), splash + several activities, Room, dialogs | 36 s |

## Does it make agents better?

On six Jetsnack tasks (features and bug reports), an agent with noemu and one without both got 6/6
([study](bench/AGENT_STUDY.md)). noemu didn't make the code better. It let the agent check its work in the running app
and say so ("after I tapped 'Clear cart', the header read 'Order (0 items)'"), where the other could only write "I haven't
seen it on screen".

## Check it yourself

`python bench/reproduce.py WORKDIR` clones the apps at pinned commits and replays every bug above with fixed steps:
10 of 10 reproduced ([results, and a second reviewer's reading of the upstream code](bench/REPRODUCED.md)).

## Next to the usual tools

[Measured in a cloud container](bench/COMPARISON.md): the Android Emulator refuses to start there (no KVM), so every
device-based tool is out; Android Lint reports 123 and 43 warnings on Jetchat and Jetsnack and none about the bugs
noemu found; an agent reading noemu's screen text spends 3.5-6x fewer tokens per look than with a screenshot.

## What else exists

- **Agent tools that drive Android** ([android-mcp](https://github.com/remymazmanian/android-mcp),
  [Android-MCP](https://github.com/CursorTouch/Android-MCP), [looktap](https://github.com/lakpriya1s/looktap)) all need
  a device, an emulator or a cloud phone.
- **Compose previews on the JVM.** [compose-ai-tools](https://klibs.io/project/yschimke/compose-ai-tools) renders
  Compose `@Preview`s through Robolectric for agents, clicks included, and Android Studio's interactive preview runs one
  composable on layoutlib. Both work at the level of a preview: no activities, navigation, saved state or rotation.
- **Robolectric and [Roborazzi](https://github.com/takahirom/roborazzi)** run app code on the JVM, in tests you write
  in advance. Back in 2014, [Jake Wharton wrote](https://jakewharton.com/android-needs-a-simulator/) that neither
  Robolectric nor layoutlib "is suited to running an application during development". We found no tool that runs a
  whole, unmodified app (its activities, navigation, rotation and process death) interactively on the JVM.
- **Research tools for rotation and data-loss bugs** ([RotDroid](https://arxiv.org/abs/2608.25425),
  [iFixDataloss](https://arxiv.org/abs/2209.08719), DDLDroid, LiveDroid) found hundreds of such bugs in real apps, with
  emulators, instrumentation or a fine-tuned vision model. noemu's check is plainer (the screen before and after) and
  runs inside your own Gradle build.

## Limits

- It is Robolectric, not a device: no GPU (shaders are drawn on the CPU), no camera, no real sensors; rendering is
  close, not pixel-identical.
- Needs a JDK 17 or newer, plus a JDK 21 installed when the build runs on 17 (Gradle finds it).
- WorkManager work that waits on constraints doesn't run on its own yet.
- It grants the app's permissions and "all files access" up front, as a user would at the first prompt; a crawl with
  `--data` can save to that file.
- `kill` rebuilds the top activity from its saved state; singletons and caches survive, unlike a real process death.
- One screen size per session (411x891 dp).
