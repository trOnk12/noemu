# Does running the app make a coding agent better?

**No, not on these tasks.** A coding agent in a sandbox without an emulator got all six right whether or not it could
run the app with noemu.

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
example, T4's check opens MY CART and expects `$54.44`. The checks pass on the original code and fail on the injected
bugs, which was verified before the runs.

## Results

| Arm | Passed | Cost | Time | Turns |
| --- | --- | --- | --- | --- |
| A, with noemu | 6 / 6 | $1.04 | 405 s | 97 |
| B, without | 6 / 6 | $0.91 | 237 s | 88 |

Raw results are in [`results/`](results/).

## What it means

- **Accuracy:** for logic-level features and bug reports, the agent reasons its way to a correct change from the code
  alone. noemu didn't raise accuracy. It cost about 15% more and took longer, since starting the app takes a minute.
- **Evidence:** with noemu the agent *checked* its work in the running app. For example: "After I tapped 'Clear
  cart', the header read 'Order (0 items)'". Without it, the agent says so plainly: "I haven't seen 'cup' return
  Cupcake on screen." That evidence is what noemu adds. It doesn't change the code the agent writes.
- **Untested:** visual work (layout, overflow, colours), apps much larger than Jetsnack, and weaker models.
