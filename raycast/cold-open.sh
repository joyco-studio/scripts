#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Cold Open
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🧊
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "url" }
# Wipes the Metal shader cache and opens the URL in a fresh temp profile so you
# get a cold load (no cache, no extensions, no compiled shaders). Your regular
# Chrome stays open; just close the page's tab first.

URL="$1"

if [ -z "$URL" ]; then
  echo "Missing URL"
  exit 1
fi

# Allow pasting without scheme (e.g. "foo.vercel.app", "localhost:3000")
if [[ ! "$URL" =~ ^https?:// ]]; then
  if [[ "$URL" =~ ^(localhost|127\.0\.0\.1)(:|/|$) ]]; then
    URL="http://$URL"
  else
    URL="https://$URL"
  fi
fi

# 1. Wipe the Metal shader caches for Chrome's GPU helper. Chrome can stay
#    open as long as the page isn't open in it (it would refill the cache
#    with the same shaders).
METAL_CACHE="$(getconf DARWIN_USER_CACHE_DIR)/com.google.Chrome.helper"
rm -rf "$METAL_CACHE/com.apple.metal" "$METAL_CACHE/com.apple.metalfe"

# 2. Open the URL in a brand-new temporary profile (a separate Chrome instance)
PROFILE="$(mktemp -d)"
open -na "Google Chrome" --args --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check "$URL"

echo "Opened $URL cold"
