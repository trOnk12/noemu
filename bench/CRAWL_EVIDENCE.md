# Does `noemu crawl` find real bugs?

`noemu crawl` taps through an app on the JVM, typing into fields and submitting them. It rotates the device there and
back on every new screen and after every action that changes one, and once per screen kills the app in the background
and reopens it. It reports:

- **problems**: crashes, text the crawl typed that a rotation lost, and views stacked again on each rotation;
- **worth a look**: anything else a rotation changed (a search bar or menu closing may be fine).

Everything below ran in a cloud container with no emulator, with the final driver (commit `bf827f5`): one crawl of
100 actions per app, 60 for the File Manager editor, under 10 minutes each including the build.

## Bugs in Google's official Compose samples, found unprompted

[android/compose-samples](https://github.com/android/compose-samples) at `0bc770fd` (2026-10-19), unmodified.

| App | Found | Cause in the code |
| --- | --- | --- |
| Jetchat | crash: `IllegalArgumentException: Key "me-8:30 PM-noemu" was already used` (send the same message twice) | message ids are `"$author-$timestamp-$content"` since `f81c4b73` "Dont use UUID", and the timestamp of a sent message is a fixed string (`R.string.now`, "8:30 PM") |
| Jetsnack | typed text lost: the search query is gone after a rotation | `rememberSearchState` holds the query in `remember`, not `rememberSaveable` (`Search.kt:123`) |
| Reply | typed text lost: the search query is gone after a rotation | `var query by remember { mutableStateOf("") }` (`ReplyAppBars.kt:58`) |
| Jetcaster | typed text lost: the search query is gone after a rotation | `var queryText by remember { mutableStateOf("") }` (`Home.kt:303`) |
| Jetsnack | worth a look: "SEE LESS" back to "SEE MORE" after a rotation | `var seeMore by remember { mutableStateOf(true) }` (`SnackDetail.kt:313`) |

The Jetchat crash needs the same text sent twice: the crawl replays a type-and-submit that changed the screen, and
found the crash in 5 of 5 fresh crawls (2 of 7 before a ticking video clock stopped hiding the screen from the crawl). By hand, every time:

```text
$ noemu type "Text input" "ok" && noemu tap Send && noemu type "Text input" "ok" && noemu tap Send
...
the app crashed: java.lang.IllegalArgumentException: Key "me-8:30 PM-ok" was already used. ...
```

## Known bugs from the apps' history

Each row is a bug the developers fixed. Where the release before the fix still builds, we crawled that release and the
fix. Where it doesn't (JitPack dependencies that are gone), we put the bug back into today's code as the fix commit
undoes it.

| Bug | How | Before the fix | After the fix |
| --- | --- | --- | --- |
| Fossify Calendar [#644](https://github.com/FossifyOrg/Calendar/issues/644) "App crashes after rotating several times" (users on Galaxy S24, Fairphone 4, Pixel 10 Pro), whose stacking [#1041](https://github.com/FossifyOrg/Calendar/pull/1041) fixed ("Don't recreate view pager fragments on orientation change") | the real releases `6b4528f24` and `7165bae72` | problem: "MainActivity: 52 elements before rotating there and back, 144 after; shown more than once now: Go to previous month, October, Go to next month", on the first rotation; found in 4 of 4 crawls | no stacking in 4 of 4 crawls |
| Fossify File Manager [#111](https://github.com/FossifyOrg/File-Manager/pull/111) "Text File Editor losing unsaved changes when rotating screen" | put back | problem: "ReadTextActivity: typed text lost: \"noemu\"" | not reported |
| Fossify Notes [`a24d3223`](https://github.com/FossifyOrg/Notes/commit/a24d3223) "Fix crash when restoring state on TextFragment" | put back | crash: `ClassCastException: MyEditText cannot be cast to MyTextView` in `TextFragment.onViewStateRestored` | no crash |
| Fossify Calculator [#305](https://github.com/FossifyOrg/Calculator/pull/305) "Fix data lost during rotation" | put back | worth a look: "MainActivity: gone after rotating there and back: text 0." (the number being entered) | not reported |
| Fossify Calendar `a8c2e66` "properly retain event fields on device rotation" | restore removed from today's `EventActivity` | crash: `UninitializedPropertyAccessException: lateinit property mEventStartDateTime` in `EventActivity.onSaveInstanceState`, in 2 of 2 fresh crawls | no crash |

## The app killed in the background

Android kills apps in the background and later rebuilds them from their saved state alone: ViewModels and everything
else in memory are gone. `noemu kill` (and the crawl, once per screen after an action changes it) does the same to the
top activity: its saved state is parcelled and unparcelled as the system does, and its ViewModels are dropped.
Singletons survive (one JVM), so this finds a subset of what a device would.

| App | Change | Crawl |
| --- | --- | --- |
| Now in Android (`a49ed25`), search query in a plain ViewModel field instead of `SavedStateHandle` | 5 lines | problem: "MainActivity: typed text lost: \"Search noemu\"; gone after the app was killed in the background and reopened" (rotations on that screen pass: a ViewModel survives them) |
| Now in Android, unmodified | none | the query survives; only the "still processing the search index" message is listed to check |

On the other apps above the kill check added no problems.

Calculator's display isn't a text field, so noemu can't tell its loss from a menu closing: it is listed as worth a
look, not as a problem.

## Today's apps, unmodified

| App | Crashes | Problems | Worth a look | Checked against the code |
| --- | --- | --- | --- | --- |
| Fossify Calculator | 0 | 0 | 0 | |
| Fossify Calendar | 0 | 0 | 1: the search bar's Back arrow gone | real (below) |
| Fossify Contacts | 0 | 0 | 1: Sort by and Filter gone after a rotation | not checked |
| Fossify Music Player | 0 | 0 | 1: the search bar's Back arrow gone | real (below) |
| Fossify Notes | 0 | 0 | 1: Undo gone after typing and rotating | real: `MainActivity` saves no state; the undo history is lost |
| Fossify File Manager (editor) | 0 | 0 | 1: the open search field gone | same kind as Notes |
| JetNews | 0 | 0 | 1: the Interests tab no longer selected | not checked |
| JetLagged (compose-samples `0bc770fd`) | 0 | 0 | 1: the chosen sleep period (6M) back to Week after a background kill | real: `var selectedTab by remember` (`JetLaggedTimeGraph.kt:52`); the activity handles rotation itself, so only the kill shows it |
| Jetchat, Jetsnack | above | | Jetchat: the open drawer closed | not checked; `rememberDrawerState` is saveable, so likely ours |
| Google's architecture-samples (to-do app, `ee66e15`) | 0 | 0 | 2: header after rotating; a task after a background kill (likely the list reloading from its database) | not checked |
| Google's Sunflower (`2a357a3`) | 0 | 0 | 1: the "Added plant to garden" message, which goes away by itself | harmless |
| Read You (Ashinch/ReadYou `d2b979c`) | 0 | 0 | 2: an icon after rotating; another screen after a background kill | not checked |

The search bar: in Fossify Commons' `MySearchMenu`, `isSearchOpen` isn't saved, and the focus listener that reopens
the search is set in `post {}`, after Android restores focus. After a rotation the query stays in the bar but the
search counts as closed, so Back leaves the app instead of closing the search. Every Fossify app with that search bar
has it.

## What noemu fixed on the way

Each was a false report that is now gone:

- The crawl tapped the screen behind an open dialog and reported a Calendar crash no user can reach. Only the window on
  top (a dialog or menu when one is open) takes touches now.
- A dialog of a finished activity stayed on screen (Robolectric keeps its window).
- `Environment.isExternalStorageManager()` threw inside Robolectric's app ops; noemu grants "all files access" as it
  grants the app's other permissions.
- Music Player crashed at launch: Robolectric connects `bindService` to nothing. noemu binds the app's own services.
- Apps reading `Build.VERSION.SDK_INT_FULL` crashed on SDK 35; noemu runs SDK 36, on a JDK 21 that Gradle finds when
  the build itself runs on 17 (Robolectric needs 21 for SDK 36).
- Screenshots of apps drawing with shaders (JetLagged's `RuntimeShader` backgrounds) failed: a plain bitmap canvas
  refuses GPU features. noemu now draws through Android's renderer, as a device does, and the shaders show.
- Robolectric's `recreate()` can fail after the new activity is up (window focus); noemu ignores only that failure,
  after a broader rule once hid a real app crash.
- Scrolling, rows cut at the screen edge, clocks and media positions are left out of the rotation comparison; a
  wholly different screen after rotating (single-activity apps) is listed to check, not as a problem;
  `configChanges="orientation|screenSize"` is honoured; GPU-only graphics (`MeshSpecification`) count as JVM limits.

## Limits

- What the crawl reaches can vary between runs (timing changes what is on screen). After the last round of fixes, each
  finding above came up in every fresh crawl we ran: Jetchat 5 of 5, the others 2 of 2 each, and the fixed Calendar
  stayed clean in 2 of 2. More runs would tighten those numbers.
- Rows marked "put back" are today's code with the historical bug re-applied, not the historical release.
