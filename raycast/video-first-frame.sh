#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Extract Video First Frame
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🎬
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "video path or URL" }

VIDEO_INPUT="$1"

if [ -z "$VIDEO_INPUT" ]; then
  echo "Error: No video path or URL provided"
  exit 1
fi

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
  echo "Error: ffmpeg is not installed"
  exit 1
fi

# Generate a temporary file for the extracted frame
TEMP_DIR=$(mktemp -d)
TEMP_FILE="$TEMP_DIR/first_frame.png"

# Extract the first frame using ffmpeg
# -ss 0 seeks to the beginning
# -vframes 1 extracts only one frame
# -f image2 forces image output format
if ! ffmpeg -y -ss 0 -i "$VIDEO_INPUT" -vframes 1 -f image2 "$TEMP_FILE" 2>/dev/null; then
  rm -rf "$TEMP_DIR"
  echo "Error: Failed to extract frame from video"
  exit 1
fi

# Check if the frame was extracted successfully
if [ ! -f "$TEMP_FILE" ]; then
  rm -rf "$TEMP_DIR"
  echo "Error: Frame extraction failed"
  exit 1
fi

# Use AppleScript to open a save dialog
SAVE_PATH=$(osascript -e 'tell application "Finder"
  activate
  set defaultName to "first_frame.png"
  set saveFile to choose file name with prompt "Save first frame as:" default name defaultName
  return POSIX path of saveFile
end tell' 2>/dev/null)

# Check if user cancelled the dialog
if [ -z "$SAVE_PATH" ]; then
  rm -rf "$TEMP_DIR"
  echo "Cancelled"
  exit 0
fi

# Ensure the file has .png extension
if [[ ! "$SAVE_PATH" =~ \.png$ ]]; then
  SAVE_PATH="${SAVE_PATH}.png"
fi

# Move the temp file to the chosen location
mv "$TEMP_FILE" "$SAVE_PATH"
rm -rf "$TEMP_DIR"

echo "Saved to $(basename "$SAVE_PATH")"
