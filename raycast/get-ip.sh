#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Get IP
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🌍

ifconfig | awk 'match($0, /(10\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5])\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5])\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5]))|(172\.(1[6-9]|2[0-9]|3[0-1])\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5])\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5]))|(192\.168\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5])\.(1?[0-9]{1,2}|2[0-4][0-9]|25[0-5]))/) {ip=substr($0, RSTART, RLENGTH); gsub(/^[[:space:]]+|[[:space:]]+$/, "", ip); gsub(/\n/, "", ip); printf "%s", ip; exit}' | pbcopy