# Android fixtures

These maintained apps give the driver gate independent expected behaviors. Both
contain a field that saves its text, a deliberately broken field that loses text
on recreation, disabled and duplicate buttons, a horizontal list, and a button
that deliberately throws `fixture deliberate crash`. The Views app also has a
hidden button. Neither app requires an external service.
The Views fields use both a generated View ID and a compiled resource ID. The
integration gate checks that generated IDs remain observable and actionable via
observation references, while only compiled IDs receive resource-name selectors.

Both apps also register `dev.noemu.fixture.FocusCrashActivity` from `shared-main`.
It launches normally, saves a marker, then deliberately throws a
`NullPointerException` when focus returns after saved-state recreation. The
integration gate requires rotation to return `failed` with that crash message.
This guards against treating a `windowFocusChanged` stack frame as permission to
suppress an exception. This fixture and driver change still need execution with
the toolchain below; source inspection is not a passing Android runtime check.

Use JDK 21, Android SDK platform 35 and build tools 35.0.0, and Gradle 8.11.1.
Generate the standard Gradle wrapper once (generated wrapper files are ignored):

```sh
gradle -p fixtures wrapper --gradle-version 8.11.1
python -m pip install -e .
NOEMU_FIXTURE=views python tests/integration_driver.py
NOEMU_FIXTURE=compose python tests/integration_driver.py
```

Run the Kotlin protocol contract tests with the same init script that adds the
driver and its dependencies to app projects:

```sh
cd fixtures
./gradlew -I ../noemu/noemu.init.gradle \
  -Dnoemu.driver="$PWD/../noemu" -Dnoemu.module=:views \
  :views:testDebugUnitTest --tests dev.noemu.ProtocolTest
```

The integration runner builds each selected fixture once, starts a clean JVM per
test, and destroys it after the test. A missing toolchain is a failure, not a
skip. These are JVM driver checks. Instrumented Android comparison is milestone C
and has not been implemented or run here.
