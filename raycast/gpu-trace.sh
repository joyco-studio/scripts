#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title GPU Trace
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🎞️
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "url" }
# @raycast.argument2 { "type": "text", "placeholder": "seconds (10)", "optional": true }
# Launches Chrome in a fresh temp profile with --trace-config-file, records the
# first N seconds (GPU/compositor/frame categories DevTools hides by default)
# and saves the trace to ~/Downloads. Load it in DevTools → Performance.

URL="$1"
DURATION="$2"
CONFIG="$(cd "$(dirname "$0")" && pwd)/trace-config.json"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if [ -z "$URL" ]; then
  echo "Missing URL"
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "Missing $CONFIG"
  exit 1
fi

if [ -n "$DURATION" ] && [[ ! "$DURATION" =~ ^[0-9]+$ ]]; then
  echo "Seconds must be a whole number"
  exit 1
fi

# Allow pasting without scheme (e.g. "localhost:3000")
if [[ ! "$URL" =~ ^https?:// ]]; then
  if [[ "$URL" =~ ^(localhost|127\.0\.0\.1)(:|/|$) ]]; then
    URL="http://$URL"
  else
    URL="https://$URL"
  fi
fi

# 1. Copy the checked-in config so each run gets its own output file
#    (and an optional duration override) without touching the original
WORKDIR="$(mktemp -d)"
RUN_CONFIG="$WORKDIR/trace-config.json"
RESULT="$HOME/Downloads/gpu-trace-$(date +%Y-%m-%d-%H%M%S).json"

cp "$CONFIG" "$RUN_CONFIG"
plutil -replace result_file -string "$RESULT" "$RUN_CONFIG"
if [ -n "$DURATION" ]; then
  plutil -replace startup_duration -integer "$DURATION" "$RUN_CONFIG"
fi
DURATION="$(plutil -extract startup_duration raw "$RUN_CONFIG")"

# 2. Launch a separate Chrome instance in a fresh profile with the config.
#    A new --user-data-dir means a new browser process, so startup tracing
#    applies even if your regular Chrome is already open.
"$CHROME" \
  --user-data-dir="$WORKDIR/profile" \
  --no-first-run \
  --no-default-browser-check \
  --trace-config-file="$RUN_CONFIG" \
  --trace-startup-format=json \
  "$URL" >/dev/null 2>&1 &

# 3. Chrome writes the file once startup_duration elapses. Wait for it to
#    exist and stop growing.
DEADLINE=$((SECONDS + DURATION + 30))
LAST_SIZE=-1
while [ $SECONDS -lt $DEADLINE ]; do
  if [ -s "$RESULT" ]; then
    SIZE="$(stat -f %z "$RESULT")"
    [ "$SIZE" = "$LAST_SIZE" ] && break
    LAST_SIZE="$SIZE"
  fi
  sleep 1
done

if [ ! -s "$RESULT" ]; then
  echo "No trace written after ${DURATION}s (config: $RUN_CONFIG)"
  exit 1
fi

open -R "$RESULT"
echo "Saved ${DURATION}s trace to $RESULT"
