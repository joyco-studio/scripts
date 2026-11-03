#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Cold Open
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🧊
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "url" }
# Quits Chrome, wipes the Metal shader cache, and opens the URL in a fresh
# temp profile so you get a true cold load (no cache, no extensions, no shaders).

URL="$1"

if [ -z "$URL" ]; then
  echo "Missing URL"
  exit 1
fi

# Allow pasting without scheme (e.g. "foo.vercel.app")
if [[ ! "$URL" =~ ^https?:// ]]; then
  URL="https://$URL"
fi

# 1. Quit Chrome and wait until it's really gone
osascript -e 'quit app "Google Chrome"' 2>/dev/null
for _ in $(seq 1 50); do
  pgrep -xq "Google Chrome" || break
  sleep 0.1
done

# 2. Wipe the Metal shader cache for Chrome's GPU helper
rm -rf "$(getconf DARWIN_USER_CACHE_DIR)/com.google.Chrome.helper/com.apple.metal"

# 3. Open the URL in a brand-new temporary profile
PROFILE="$(mktemp -d)"
open -na "Google Chrome" --args --user-data-dir="$PROFILE" "$URL"

echo "Opened $URL (profile: $PROFILE)"
