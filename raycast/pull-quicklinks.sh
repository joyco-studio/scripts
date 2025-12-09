#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Pull Quicklinks
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🔗
# @raycast.packageName JOYCO Developer Tools

# Pull quicklinks.json from registry and save with timestamp

DATE=$(date +"%m-%d-%Y")
OUTPUT_FILE="$HOME/Downloads/quicklinks-pull-${DATE}.json"

curl -s "https://registry.joyco.studio/quicklinks.json" -o "$OUTPUT_FILE"

if [ $? -eq 0 ] && [ -s "$OUTPUT_FILE" ]; then
    LINK_COUNT=$(jq 'length' "$OUTPUT_FILE" 2>/dev/null)
    
    if [ -n "$LINK_COUNT" ]; then
        echo "✓ Downloaded $LINK_COUNT quicklinks to $OUTPUT_FILE"
    else
        echo "✓ Downloaded quicklinks.json to $OUTPUT_FILE"
    fi
else
    echo "✗ Failed to download quicklinks.json"
    exit 1
fi
