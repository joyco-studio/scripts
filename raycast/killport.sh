#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Kill Port
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 💀
# @raycast.packageName Developer Utils
# @raycast.argument1 { "type": "text", "placeholder": "port number" }

PORT=$1

if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid port: $PORT"
  exit 1
fi

PID=$(lsof -ti :$PORT)

if [ -z "$PID" ]; then
  echo "No process found on port $PORT"
  exit 0
fi

kill -9 $PID 2>/dev/null

if [ $? -eq 0 ]; then
  echo "Killed process $PID on port $PORT"
else
  echo "Failed to kill process on port $PORT"
  exit 1
fi

