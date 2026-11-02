#!/usr/bin/env bash
# Provision the documented gate toolchain into one user-owned directory, without
# sudo or changes outside it: Temurin JDK 21, Gradle 8.11.1, Android SDK platform 35
# and build tools 35.0.0. Rerunning skips installed components.
#
# Usage: scripts/bootstrap-toolchain.sh [dir]     (default: ~/tools/noemu)
# Then:  . <dir>/env.sh
set -euo pipefail

root=${1:-"$HOME/tools/noemu"}
gradle_version=8.11.1
cmdline_tools=11076708  # Linux command-line tools 12.0
cmdline_sha256=2d2d50857e4eb553af5a6dc3ad507a17adf43d115264b1afc116f95c92e5e258
sdk_packages=("platforms;android-35" "build-tools;35.0.0" "platform-tools")

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ;;
  *) echo "bootstrap-toolchain: only Linux x86_64 is scripted" >&2; exit 1 ;;
esac
mkdir -p "$root/downloads"

if [ ! -x "$root/jdk/bin/javac" ]; then
  meta=$(curl -fsSL 'https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=linux&vendor=eclipse')
  link=$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["binary"]["package"]["link"])')
  sum=$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["binary"]["package"]["checksum"])')
  curl -fsSL -o "$root/downloads/jdk.tar.gz" "$link"
  echo "$sum  $root/downloads/jdk.tar.gz" | sha256sum -c -
  rm -rf "$root/jdk.tmp"
  mkdir "$root/jdk.tmp"
  tar -xzf "$root/downloads/jdk.tar.gz" -C "$root/jdk.tmp" --strip-components=1
  mv "$root/jdk.tmp" "$root/jdk"
fi

if [ ! -x "$root/gradle/bin/gradle" ]; then
  archive="$root/downloads/gradle.zip"
  curl -fsSL -o "$archive" "https://services.gradle.org/distributions/gradle-$gradle_version-bin.zip"
  sum=$(curl -fsSL "https://services.gradle.org/distributions/gradle-$gradle_version-bin.zip.sha256")
  echo "$sum  $archive" | sha256sum -c -
  rm -rf "$root/gradle.tmp"
  mkdir "$root/gradle.tmp"
  unzip -q "$archive" -d "$root/gradle.tmp"
  mv "$root/gradle.tmp/gradle-$gradle_version" "$root/gradle"
  rmdir "$root/gradle.tmp"
fi

if [ ! -x "$root/android-sdk/cmdline-tools/latest/bin/sdkmanager" ]; then
  archive="$root/downloads/cmdline-tools.zip"
  curl -fsSL -o "$archive" \
    "https://dl.google.com/android/repository/commandlinetools-linux-${cmdline_tools}_latest.zip"
  echo "$cmdline_sha256  $archive" | sha256sum -c -
  rm -rf "$root/android-sdk/cmdline-tools"
  mkdir -p "$root/android-sdk/cmdline-tools"
  unzip -q "$archive" -d "$root/android-sdk/cmdline-tools"
  mv "$root/android-sdk/cmdline-tools/cmdline-tools" "$root/android-sdk/cmdline-tools/latest"
fi

cat > "$root/env.sh" <<EOT
export JAVA_HOME="$root/jdk"
export ANDROID_HOME="$root/android-sdk"
export ANDROID_SDK_ROOT="$root/android-sdk"
export PATH="$root/jdk/bin:$root/gradle/bin:$root/android-sdk/cmdline-tools/latest/bin:$root/android-sdk/platform-tools:\$PATH"
EOT
. "$root/env.sh"

set +o pipefail  # sdkmanager closes the pipe from yes
yes | sdkmanager --licenses >/dev/null
set -o pipefail
sdkmanager "${sdk_packages[@]}" >/dev/null

java -version 2>&1 | head -1
gradle --version | grep -E '^Gradle '
sdkmanager --list_installed 2>/dev/null | grep -E 'platforms;|build-tools;|platform-tools'
