#!/usr/bin/env bash
# Run every milestone A check against one committed revision, from a fresh clone.
# Setup failures stop immediately; independent checks continue after test failures.
# Fixture logs and JUnit XML are retained per check, with a bounded hash manifest.
# Usage: scripts/gate.sh [revision] [empty-results-dir]
# Needs JDK 21, Gradle 8.11.1, ANDROID_HOME; see bootstrap-toolchain.sh.
set -euo pipefail
# The wheel check must not import packages injected by the invoking shell.
unset PYTHONPATH PYTHONHOME

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
repo=$(git -C "$script_dir" rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify --end-of-options "${1:-HEAD}^{commit}")
results=${2:-$(mktemp -d)}
mkdir -p -- "$results"
results=$(cd -- "$results" && pwd -P)
if [ -n "$(ls -A -- "$results")" ]; then
  echo "gate: results directory must be empty: $results" >&2
  exit 2
fi
work=$(mktemp -d)
failed=0
runner_pid=""
check_seconds=${NOEMU_GATE_CHECK_SECONDS:-900}
setup_seconds=${NOEMU_GATE_SETUP_SECONDS:-300}
grace_seconds=${NOEMU_GATE_KILL_GRACE_SECONDS:-5}
printf 'revision %s\nstatus incomplete\n' "$revision" >"$results/result.txt"

cleanup() {
  local status=$?
  trap - EXIT
  if [ "$status" -eq 0 ]; then
    if [ -n "$work" ]; then rm -rf -- "$work"; fi
  else
    echo "gate did not pass; workspace retained at $work" >&2
    echo "setup/check logs: $results" >&2
    echo "Remove that directory when its diagnostics are no longer needed." >&2
  fi
  exit "$status"
}
trap cleanup EXIT
cancel() {
  trap '' INT TERM
  if [ -n "$runner_pid" ]; then
    kill -TERM "$runner_pid" 2>/dev/null || true
    wait "$runner_pid" || true
  fi
  exit "$1"
}
trap 'cancel 130' INT
trap 'cancel 143' TERM

bounded() {
  local seconds=$1 status
  shift
  python3 "$script_dir/gate_command.py" --seconds "$seconds" --grace "$grace_seconds" -- "$@" &
  runner_pid=$!
  if wait "$runner_pid"; then status=0; else status=$?; fi
  runner_pid=""
  return "$status"
}

check() {  # check NAME COMMAND...
  local name=$1 start status
  shift
  start=$SECONDS
  if bounded "$check_seconds" bash -e -u -o pipefail -c '"$@"' _ "$@" >"$results/$name.log" 2>&1; then status=0; else status=$?; fi
  printf '%-22s exit=%d %4ds\n' "$name" "$status" "$((SECONDS - start))" | tee -a "$results/summary.txt"
  if [ "$status" -ne 0 ]; then failed=1; fi
  if ! bounded 30 python3 "$script_dir/gate_artifacts.py" "$work/src" "$results/artifacts/$name" \
      >"$results/$name-artifacts.log" 2>&1; then
    echo "artifact capture incomplete for $name; see its manifest/log" | tee -a "$results/summary.txt"
    failed=1
  fi
}

protocol() {
  (cd "$work/src/fixtures" && ./gradlew --no-daemon --console=plain -I ../noemu/noemu.init.gradle \
    -Dnoemu.driver="$PWD/../noemu" "-Dnoemu.module=:$1" ":$1:testDebugUnitTest" --tests dev.noemu.ProtocolTest)
}

driver() {
  NOEMU_FIXTURE=$1 NOEMU_SHOTS=0 "$work/installed/bin/python" "$work/src/tests/integration_driver.py"
}

installed() {
  (cd "$work" && "$work/installed/bin/noemu" --help >/dev/null &&
    "$work/installed/bin/python" -c 'from pathlib import Path; import noemu, sys; assert Path(noemu.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), noemu.__file__' &&
    "$work/installed/bin/python" "$work/src/tests/mcp_stdio.py")
}

wheel_install() {
  "$work/installed/bin/python" -m pip install -q "$work"/src/dist/*.whl
}

prepare() {
  git clone -q --no-hardlinks "$repo" "$work/src"
  git -C "$work/src" checkout -q --detach "$revision"
  actual=$(git -C "$work/src" rev-parse --verify HEAD)
  if [ "$actual" != "$revision" ]; then
    echo "gate: checked-out revision $actual differs from requested $revision" >&2
    exit 2
  fi
  python3 -m venv "$work/dev"
  python3 -m venv "$work/installed"
}
# Run outside an `if`: errexit must remain enabled throughout setup.
export work repo revision
export -f prepare protocol driver installed wheel_install
bounded "$setup_seconds" bash -e -u -o pipefail -c prepare >"$results/setup.log" 2>&1
actual=$(git -C "$work/src" rev-parse --verify HEAD)
export actual

environment() {
  echo "revision $revision"
  echo "checked-out $actual"
  uname -sr
  python3 --version
  java -version 2>&1 | head -1
  gradle --version | grep -E '^Gradle '
}
export -f environment
bounded "$setup_seconds" bash -e -u -o pipefail -c environment >"$results/environment.txt" 2>&1
cat "$results/environment.txt"

check dev-install "$work/dev/bin/python" -m pip install -q -e "$work/src" build
check python-unit bash -c 'cd "$1" && "$2" -m unittest discover -s tests -t .' _ "$work/src" "$work/dev/bin/python"
check wheel-build "$work/dev/bin/python" -m build "$work/src"
check wheel-install wheel_install
check mcp-stdio-installed installed
check gradle-wrapper gradle -p "$work/src/fixtures" wrapper --gradle-version 8.11.1
check protocol-views protocol views
check protocol-compose protocol compose
check driver-views driver views
check driver-compose driver compose
check dependency-inventory "$work/installed/bin/python" -m pip freeze

if [ "$failed" -eq 0 ]; then
  if rm -rf -- "$work"; then work=""; else failed=1; fi
fi
status=failed
if [ "$failed" -eq 0 ]; then status=passed; fi
printf 'revision %s\nstatus %s\n' "$revision" "$status" >"$results/result.txt"
echo "results $results"
if [ "$failed" -eq 0 ]; then echo "gate PASSED $revision"; else echo "gate FAILED $revision"; fi
exit "$failed"
