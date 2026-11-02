#!/bin/bash
# Usage, from an Android project's root: bash probe.sh :module [testTask]
# Runs Probe.kt with noemu's driver in that module's unit tests, as noemu adds it, and prints its PROBE lines.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
noemu=$(cd "$here/../.." && pwd)
[ -d "$noemu/driver" ] || noemu="$noemu/noemu"  # the standalone repository keeps the driver in the package
module=${1:-:app}; task=${2:-testDebugUnitTest}
work=$(mktemp -d)
trap 'rm -rf "${work:?}"' EXIT
cp -r "$noemu/driver" "$noemu/noemu.init.gradle" "$work/" && cp "$here/Probe.kt" "$work/driver/"
dir=$(echo "${module#:}" | tr : /)
result="$dir/build/test-results/$task/TEST-dev.noemu.Probe.xml"
rm -f "$result"  # never print an earlier run's lines
status=0
if bash ./gradlew -q --console=plain -I "$work/noemu.init.gradle" -Dnoemu.module="$module" -Dnoemu.driver="$work" \
    "$module:$task" --tests dev.noemu.Probe; then status=0; else status=$?; fi
[ -f "$result" ] || { echo "probe: no result; the build failed (see above)" >&2; exit 1; }
grep -h "PROBE" "$result" | sed 's/.*CDATA\[//'
exit "$status"  # Preserve build/test failure even when it printed diagnostic lines.
