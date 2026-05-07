#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title MD Viewer
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 📝
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "markdown file path or text" }

INPUT="$1"

if [ -z "$INPUT" ]; then
  echo "✗ No input provided"
  exit 1
fi

if [ -f "$INPUT" ]; then
  CONTENT=$(cat "$INPUT")
  FILENAME=$(basename "$INPUT")
else
  CONTENT="$INPUT"
  FILENAME="untitled.md"
fi

ENCODED=$(printf '%s' "$CONTENT" | gzip -c | base64 | tr -d '\n' | tr '+/' '-_' | tr -d '=')
FILENAME_ENCODED=$(printf '%s' "$FILENAME" | jq -sRr @uri)

open "https://md.joyco.studio/?filename=$FILENAME_ENCODED&content=$ENCODED"

echo "✓ Opened in md.joyco.studio"
