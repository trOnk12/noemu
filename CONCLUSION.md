# noemu: conclusion (shelved 2026-10-25)

**Decision: shelved.** noemu works, and it found real bugs, but it does not open a field nobody holds, and no other
requirement we could check makes it worth publishing as a project of its own. Two independent peer reviews (prior
art; claims and code) settled it.

## Why

- **Running the whole app on the JVM already exists.** Robolectric's own
  [Simulator](https://robolectric.org/simulator/) (since 4.15, 2025; "highly experimental") runs an app's main
  activity interactively under Robolectric, from `./gradlew simulate<variant>`. noemu is built on Robolectric, so the
  owners of its foundation are already in this space. Our prior-art search missed it: it looked at agent tools,
  emulators and research crawlers, not at the base library's own releases.
- **The crawl's check is known.** Rotating or killing the app on every screen and comparing the screen before and
  after is the oracle of ALARic (ISSTA'18), Quantum (ICST'14) and DLD (ISSTA'20). Data loss on restart is also
  found statically, with no device (DDLDroid, ISSTA'23; KREfinder; LiveDroid).
- **The platform vendor is moving in.** Google previewed an Android CLI for coding agents in April 2026 (with
  emulators). A headless, text-driven mode is a small step for Robolectric's Simulator or for Google.
- **The extra value didn't show.** In the agent study an agent with noemu did no better (6/6 against 6/6) and spent
  about 15% more. Four of the five bugs found in Google's samples are one pattern (`remember` where
  `rememberSaveable` was needed), which a lint rule would catch more cheaply.
- **Fidelity gaps.** `kill` rebuilds only the top activity in the same JVM, so singletons survive: the most common
  real process-death bug stays invisible. The review also found a crash classifier that can file a real crash with no
  app frame on the stack (a Compose layout error, a `Parcel` failure in `kill`) as a JVM limit, and taps that reach
  disabled buttons.

## What it produced

- Five bugs in Google's [compose-samples](https://github.com/android/compose-samples), each checked in the running
  app before and after the fix: Jetchat crashes when the same message is sent twice; Jetsnack, Reply and Jetcaster
  lose the search query on rotation (Reply also when it switches to two panes); Jetsnack's "see more" and
  JetLagged's sleep period reset. Fixes are on branches ready for an upstream pull request.
- A historical-bug replay for Fossify apps, with raw logs ([REPRODUCED.md](bench/REPRODUCED.md)).

## Lessons for the next idea

1. **Search the foundation first.** Before building on a library or platform, read its own changelog and roadmap
   for the last two years, and the vendor's announcements. That is where the strongest competitor came from, twice.
2. **Kill test in the first hour.** Name the closest prior art and what the idea does that it can't. If the answer
   is a feature its owner could add in a release, stop.
3. **Value must be measurable, not shown.** "Agents can now see the screen" did not change outcomes. The next idea
   needs a metric that moves (bugs shipped, time, cost) in a test designed to show a difference.
4. **Bugs found should be ones cheaper tools miss.** If a lint rule or static analysis finds the same class, the
   tool is a demo, not a product.
5. **Peer review before publishing, not after.** Both reviews found what our own checks missed.
