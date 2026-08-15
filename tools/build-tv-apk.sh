#!/usr/bin/env bash
#
# Build the Android TV shell.
#
# Deliberately not Gradle. The app has no third-party dependencies at all — no
# AndroidX, no leanback library, just android.jar — so the whole build is four
# tools from the SDK. That keeps it buildable on a CI runner without downloading
# a Gradle distribution and an Android plugin, and keeps the thing anyone has to
# read in order to trust the APK down to this file.
#
# Usage:  tools/build-tv-apk.sh [output.apk]
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/android"
OUT="${1:-$ROOT/build/homesh-tv.apk}"
WORK="$ROOT/build/tv"

MIN_SDK=21          # Lollipop: the floor for Android TV set-top boxes
TARGET_SDK=36

# ── Locate the SDK ───────────────────────────────────────────────────────────
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$SDK" ]; then
  for candidate in "$LOCALAPPDATA/Android/Sdk" "$HOME/Android/Sdk" "$HOME/Library/Android/sdk"; do
    [ -d "$candidate" ] && SDK="$candidate" && break
  done
fi
[ -n "$SDK" ] && [ -d "$SDK" ] || { echo "Android SDK not found. Set ANDROID_HOME." >&2; exit 1; }

# Newest build-tools present, so this does not pin a version that has to be
# chased every time the SDK updates.
BT_DIR="$(ls -1d "$SDK"/build-tools/*/ 2>/dev/null | sort -V | tail -1)"
[ -n "$BT_DIR" ] || { echo "No build-tools in $SDK." >&2; exit 1; }
BT="${BT_DIR%/}"

PLATFORM_DIR="$(ls -1d "$SDK"/platforms/android-*/ 2>/dev/null | sort -V | tail -1)"
[ -n "$PLATFORM_DIR" ] || { echo "No platform in $SDK." >&2; exit 1; }
ANDROID_JAR="${PLATFORM_DIR%/}/android.jar"

# Windows keeps .exe suffixes; everything else does not.
EXE=""
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) EXE=".exe" ;; esac

# Under Git Bash the SDK tools are native Windows binaries. The shell rewrites
# path-looking arguments for them, but it cannot rewrite the contents of an
# @response file — so those are converted explicitly.
# Both branches end with a newline: these feed @response files one path per
# line, and a fallback that omitted it ran every path together into one.
if command -v cygpath >/dev/null 2>&1; then
  winpath() { cygpath -w "$1"; }
else
  winpath() { printf '%s
' "$1"; }
fi

AAPT2="$BT/aapt2$EXE"
ZIPALIGN="$BT/zipalign$EXE"
APKSIGNER="$BT/apksigner"
[ -f "$APKSIGNER" ] || APKSIGNER="$BT/apksigner.bat"
D8="$BT/d8"
[ -f "$D8" ] || D8="$BT/d8.bat"

# ── Locate a JDK ─────────────────────────────────────────────────────────────
if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/javac$EXE" ]; then
  JAVAC="$JAVA_HOME/bin/javac$EXE"; KEYTOOL="$JAVA_HOME/bin/keytool$EXE"
elif command -v javac >/dev/null 2>&1; then
  JAVAC="javac"; KEYTOOL="keytool"
elif [ -x "/c/Program Files/Android/Android Studio/jbr/bin/javac.exe" ]; then
  # Android Studio ships a JDK; on a machine with no standalone one, use it.
  JAVAC="/c/Program Files/Android/Android Studio/jbr/bin/javac.exe"
  KEYTOOL="/c/Program Files/Android/Android Studio/jbr/bin/keytool.exe"
  export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"
else
  echo "No JDK found. Set JAVA_HOME." >&2; exit 1
fi

echo "SDK       $SDK"
echo "tools     $(basename "$BT")"
echo "platform  $(basename "${PLATFORM_DIR%/}")"

rm -rf "$WORK"
mkdir -p "$WORK/res" "$WORK/gen" "$WORK/classes" "$WORK/dex" "$(dirname "$OUT")"

# ── 0. Test the address parser ───────────────────────────────────────────────
# It is plain Java by design, so it runs here on the build machine rather than
# only once it is on a television with a remote control as the only input.
JAVA_BIN="$(dirname "$JAVAC")/java$EXE"
[ -x "$JAVA_BIN" ] || JAVA_BIN="java"
mkdir -p "$WORK/testclasses"
"$JAVAC" -nowarn -encoding UTF-8 --release 17 -d "$WORK/testclasses"   "$(winpath "$SRC/java/com/homesh/tv/ServerAddress.java")"   "$(winpath "$SRC/test/ServerAddressTest.java")"
"$JAVA_BIN" -cp "$(winpath "$WORK/testclasses")" ServerAddressTest

# ── 1. Resources ─────────────────────────────────────────────────────────────
"$AAPT2" compile --dir "$SRC/res" -o "$WORK/res.zip"

"$AAPT2" link \
  -o "$WORK/base.apk" \
  --manifest "$SRC/AndroidManifest.xml" \
  -I "$ANDROID_JAR" \
  -R "$WORK/res.zip" \
  --java "$WORK/gen" \
  --min-sdk-version "$MIN_SDK" \
  --target-sdk-version "$TARGET_SDK" \
  --auto-add-overlay

# ── 2. Compile ───────────────────────────────────────────────────────────────
# Release 17 rather than the JDK's own level: d8 rejects newer class files, and
# nothing here needs a language feature beyond it.
find "$SRC/java" "$WORK/gen" -name '*.java' | while read -r f; do winpath "$f"; done   > "$WORK/sources.txt"
"$JAVAC" -nowarn -encoding UTF-8 --release 17 \
  -classpath "$ANDROID_JAR" \
  -d "$WORK/classes" \
  @"$WORK/sources.txt"

# ── 3. Dex ───────────────────────────────────────────────────────────────────
find "$WORK/classes" -name '*.class' | while read -r f; do winpath "$f"; done   > "$WORK/classes.txt"
"$D8" --release --lib "$ANDROID_JAR" --min-api "$MIN_SDK" \
  --output "$WORK/dex" @"$WORK/classes.txt"

# ── 4. Package ───────────────────────────────────────────────────────────────
cp "$WORK/base.apk" "$WORK/unsigned.apk"

# aapt2 produces the APK without the code in it, so the dex files are added
# afterwards. `zip` is the obvious tool and is present on CI; Git Bash ships
# without it, so Python stands in — one of the two is always there.
if command -v zip >/dev/null 2>&1; then
  (cd "$WORK/dex" && zip -q "$WORK/unsigned.apk" classes*.dex)
else
  # Tested by running it, not by looking it up: on Windows `python3` resolves to
  # a Microsoft Store stub that exists but does nothing.
  PY_BIN=""
  for candidate in python3 python py; do
    if "$candidate" -c "import zipfile" >/dev/null 2>&1; then PY_BIN="$candidate"; break; fi
  done
  [ -n "$PY_BIN" ] || { echo "Need either zip or python to package the APK." >&2; exit 1; }
  "$PY_BIN" - "$(winpath "$WORK/unsigned.apk")" "$(winpath "$WORK/dex")" <<'EOF'
import pathlib, sys, zipfile

apk, dex_dir = sys.argv[1], pathlib.Path(sys.argv[2])
with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
    for dex in sorted(dex_dir.glob("classes*.dex")):
        z.write(dex, dex.name)
EOF
fi

"$ZIPALIGN" -f -p 4 "$WORK/unsigned.apk" "$WORK/aligned.apk"

# ── 5. Sign ──────────────────────────────────────────────────────────────────
# The keystore is generated locally and never committed. It is not a release
# key; it exists because Android refuses to install anything unsigned. Keeping
# it out of the repository also keeps every install upgradeable on the machine
# that built it, and unforgeable by anyone reading the source.
KEYSTORE="${HOMESH_TV_KEYSTORE:-$ROOT/.local/homesh-tv.jks}"
mkdir -p "$(dirname "$KEYSTORE")"
if [ ! -f "$KEYSTORE" ]; then
  echo "Generating a signing key at $KEYSTORE (first build only)"
  "$KEYTOOL" -genkeypair -v \
    -keystore "$KEYSTORE" -storepass homesh -keypass homesh \
    -alias homesh -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Homesh TV, OU=Homesh, O=Homesh" >/dev/null
fi

"$APKSIGNER" sign \
  --ks "$KEYSTORE" --ks-pass pass:homesh --key-pass pass:homesh --ks-key-alias homesh \
  --out "$OUT" "$WORK/aligned.apk"

"$APKSIGNER" verify "$OUT" >/dev/null
echo
echo "Built $OUT"
ls -la "$OUT" | awk '{print "  " $5 " bytes"}'
