# Does running the app make a coding agent better?

**Historical, provisional results.** The original scorer reported six passing tasks
in each arm. A subsequent review found it ignored command exit codes and mixed
stderr into UI observations. That could score a failed action as a pass. The raw
records below are preserved, but have not been rescored with the repaired harness;
they do not establish task accuracy or a measured benefit from NoEmu.

The current scorer requires each operation to succeed before checking stdout.
Version 2 score records distinguish `passed`, `failed`, `unsupported`, and
`inconclusive`, preserve operation and cleanup errors, and keep timeout output.
The `check` command exits nonzero if any check does not pass, or none ran.
Its existing text assertions remain narrow heuristics: for example, T1 does not
prove that a number belongs to the cart badge. Replayable semantic assertions and
Android verification remain roadmap work.

Install NoEmu into the Python environment used to run these scripts; they now use
`python -m noemu`, including from app directories. An inherited `NOEMU_ID` is
removed so a benchmark cannot redirect actions to a shell-selected fleet instance.
No new app/model runs accompanied this harness repair. The `run` subcommand invokes
Claude and is separate from the local regression tests.

## Setup

Six tasks on Google's Jetsnack sample (Jetpack Compose), each in a fresh copy of the project
([`agent_study.py`](agent_study.py)):

| Task | Kind |
| --- | --- |
| T1 Cart badge on the MY CART tab (sum of quantities) | feature |
| T2 "Clear cart" button that empties the cart | feature |
| T3 Search for "cup" finds nothing (case-sensitive match injected) | bug report |
| T4 Cart subtotal wrong (quantities ignored, injected) | bug report |
| T5 ADD TO CART on the detail screen does nothing: make it add to the cart (a separate ViewModel instance would compile and do nothing) | feature |
| T6 Filter chips don't select (toggle injected) | bug report |

- **Arm A:** Gradle and noemu.
- **Arm B:** Gradle only, which is what a cloud sandbox has.

Both arms got the same prompt and `claude -p` (Claude Code, headless).

Each result was scored by a **hidden check that runs the app with noemu** and looks for the asked-for behaviour. For
example, T4's check opens MY CART and expects `$54.44`. The original study reported
checking the injected bug/fix pairs before running. That statement predates the
scorer repair and has not been independently revalidated.

## Historical results reported by the original scorer

| Arm | Passed | Cost | Time | Turns |
| --- | --- | --- | --- | --- |
| A, with noemu | 6 / 6 | $1.04 | 405 s | 97 |
| B, without | 6 / 6 | $0.91 | 237 s | 88 |

Raw results are in [`results/`](results/).

## What it means

- **Accuracy:** the original scorer reported no difference. Its defects prevent
  treating that as a validated comparison. The recorded cost and timing describe
  those historical runs only.
- **Evidence:** with noemu the agent *checked* its work in the running app. For example: "After I tapped 'Clear
  cart', the header read 'Order (0 items)'". Without it, the agent says so plainly: "I haven't seen 'cup' return
  Cupcake on screen." That evidence is what noemu adds. It doesn't change the code the agent writes.
- **Untested:** visual work (layout, overflow, colours), apps much larger than Jetsnack, and weaker models.
