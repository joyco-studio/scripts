#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Toggle Dock Animation
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🚀
# @raycast.packageName JOYCO Developer Tools

current=$(defaults read com.apple.dock autohide-time-modifier 2>/dev/null)

if [ "$current" = "0" ]; then
  defaults delete com.apple.dock autohide-time-modifier 2>/dev/null
  defaults delete com.apple.dock autohide-delay 2>/dev/null
  echo "Dock animation: ON"
else
  defaults write com.apple.dock autohide-time-modifier -float 0
  defaults write com.apple.dock autohide-delay -float 0
  echo "Dock animation: OFF"
fi

killall Dock
