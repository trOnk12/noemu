#!/usr/bin/env bash
# Run every milestone A check against one committed revision, from a fresh clone
# with freshly resolved dependencies, so uncommitted files cannot affect the result.
# Every check runs even after a failure. The exit status is nonzero if any failed.
#
# Usage: scripts/gate.sh [revision] [results-dir]   (default: HEAD, a new temp dir)
# Needs JDK 21, Gradle 8.11.1, ANDROID_HOME; see scripts/bootstrap-toolchain.sh.
set -uo pipefail

repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse "${1:-HEAD}^{commit}")
results=${2:-$(mktemp -d)}
work=$(mktemp -d)
mkdir -p "$results"
failed=0

check() {  # check NAME COMMAND...
  local name=$1 start status
  shift
  start=$SECONDS
  "$@" >"$results/$name.log" 2>&1
  status=$?
  printf '%-22s exit=%d %4ds\n' "$name" "$status" "$((SECONDS - start))" | tee -a "$results/summary.txt"
  [ "$status" -eq 0 ] || failed=1
}

protocol() {
  (cd "$work/src/fixtures" && ./gradlew --no-daemon --console=plain -I ../noemu/noemu.init.gradle \
    -Dnoemu.driver="$PWD/../noemu" "-Dnoemu.module=:$1" ":$1:testDebugUnitTest" --tests dev.noemu.ProtocolTest)
}

driver() {
  NOEMU_FIXTURE=$1 NOEMU_SHOTS=0 "$work/dev/bin/python" "$work/src/tests/integration_driver.py"
}

installed() {
  (cd "$work" && "$work/installed/bin/noemu" --help >/dev/null &&
    "$work/installed/bin/python" -c 'import noemu, sys; sys.exit("/src/noemu" in noemu.__file__)' &&
    "$work/installed/bin/python" "$work/src/tests/mcp_stdio.py")
}

{
  echo "revision $revision"
  uname -sr
  python3 --version
  java -version 2>&1 | head -1
  gradle --version | grep -E '^Gradle '
} | tee "$results/environment.txt"

git clone -q --no-hardlinks "$repo" "$work/src"
git -C "$work/src" checkout -q "$revision"
python3 -m venv "$work/dev"
python3 -m venv "$work/installed"

check dev-install "$work/dev/bin/python" -m pip install -q -e "$work/src" build
check python-unit bash -c "cd '$work/src' && '$work/dev/bin/python' -m unittest discover -s tests -t ."
check wheel-build "$work/dev/bin/python" -m build "$work/src"
check wheel-install bash -c "'$work/installed/bin/python' -m pip install -q '$work'/src/dist/*.whl"
check mcp-stdio-installed installed
check gradle-wrapper gradle -p "$work/src/fixtures" wrapper --gradle-version 8.11.1
check protocol-views protocol views
check protocol-compose protocol compose
check driver-views driver views
check driver-compose driver compose

"$work/installed/bin/python" -m pip list 2>/dev/null | grep -Ei '^(noemu|mcp|psutil) ' >>"$results/environment.txt"
rm -rf "$work"
echo "results $results"
[ "$failed" -eq 0 ] && echo "gate PASSED $revision" || echo "gate FAILED $revision"
exit "$failed"
