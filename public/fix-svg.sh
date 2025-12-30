#!/bin/bash
set -euo pipefail

TRANSFORM_URL="https://s.joyco.studio/fix-svg-jsx-attrs.js"
TRANSFORM_FILE=$(mktemp)

trap 'rm -f "$TRANSFORM_FILE"' EXIT

curl -fsSL "$TRANSFORM_URL" -o "$TRANSFORM_FILE"
find . -name "*.tsx" -type f ! -path "./node_modules/*" -print0 | xargs -0 pnpx jscodeshift --parser=tsx -t "$TRANSFORM_FILE"