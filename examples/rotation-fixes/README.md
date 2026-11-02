# Rotation fixes

`broken.kt` loses a counter when the activity is recreated. `candidate-a.kt` and
`candidate-b.kt` are two proposed fixes. Both behave correctly until the device
rotates. Only `candidate-b.kt` saves the state itself.

`compare.py` copies each variant into the Views fixture in its own git worktree,
builds it into its own generation, and runs one journey on each, concurrently:
open, tap three times, rotate, tap once more. It reports each variant's verdict and
first failing step. Run it from a checkout with the fixture wrapper generated (see
`fixtures/README.md`), with a private fleet directory:

```sh
NOEMU_HOME=$(mktemp -d) python examples/rotation-fixes/compare.py --out report.json
```

The fixture's `CounterActivity` is the broken variant. Results come from the JVM
simulation; nothing here has been verified on Android.
