# Reproduced from scratch

[`reproduce.py`](reproduce.py) re-checks each bug noemu claims to find: it clones the app at a pinned commit, re-applies
a historical bug where the case says so, drives the app with fixed noemu commands (no random crawl) and checks the
output. Run on 2026-10-24 in a cloud container with no emulator, with noemu installed from GitHub:

```bash
pip install git+https://github.com/trOnk12/noemu
python bench/reproduce.py WORKDIR
```

| Case | Claim | Result |
| --- | --- | --- |
| jetchat-same-message-twice | Jetchat (android/compose-samples `0bc770fd`) crashes when the same message is sent twice | PASS |
| jetsnack-search-rotation | Jetsnack (same commit) loses the search query on rotation | PASS |
| reply-search-rotation | Reply (same commit) loses the search query on rotation (`ReplyAppBars.kt:58`, `remember`) | PASS |
| jetcaster-search-rotation | Jetcaster (same commit) loses the search query on rotation (`Home.kt:303`, `remember`) | PASS |
| calendar-644-before | Fossify Calendar `6b4528f24`, before #1041: each rotation stacks the month view again | PASS |
| calendar-644-after | Fossify Calendar `7165bae72`, with #1041: no stacking | PASS |
| file-manager-111-back | Fossify File Manager with #111 undone: the editor loses unsaved text on rotation | PASS |
| file-manager-111-fixed | Fossify File Manager as shipped: the edit survives | PASS |
| calculator-listener-leak | A listener left on an app-wide object: leak reported with its chain (`ThemeChanges.listeners` → lambda → `MainActivity`) | PASS |
| calculator-no-leak | Fossify Calculator as shipped: no app leak | PASS |

10 of 10 (Reply and Jetcaster added on 2026-10-25). Things went wrong on the way and are recorded here as they happened: runs ran out of disk or hit a Gradle cache the full disk had
corrupted (re-run on a clean cache, all passed), and the check for `calculator-no-leak` first misread noemu's "no destroyed
activity is still in memory" as a leak (the check was fixed; noemu's answer was right).

## Checked by a second reviewer

A separate agent, given only the claims, read the upstream source at the same commits without running anything:

| Claim | Verdict |
| --- | --- |
| Jetchat crash | Confirmed: `ConversationUiState.kt:49` builds ids from author, the fixed `R.string.now` and the text (`f81c4b73`, "Dont use UUID"); `Conversation.kt:486` keys the list by it. Two video messages with the same caption collide too. |
| Jetsnack search | Confirmed: `rememberSearchState` uses `remember`; no ViewModel or saved state holds the query; the activity is recreated on rotation. |
| Jetsnack "see more" | Confirmed, minor. |
| Calendar #644 | Likely: before #1041, `onCreate` added a fragment holder on every recreation while the FragmentManager restored the old ones; 52 and 144 match 6 shared elements plus 1 and then 3 holders of 46. #1036 fixed adapter references, not the stacking. |
| Fossify search bar | Confirmed by reading the restore order: focus comes back before the listener that opens the search is attached, so Back leaves the screen. |
| "Nothing else does this" | Overstated as first written: compose-ai-tools renders Compose previews through Robolectric for agents, clicks included. The claim holds for a whole, unmodified app (activities, navigation, rotation, process death) and now says so. |
