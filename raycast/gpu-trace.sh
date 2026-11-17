#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title GPU Trace
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🎞️
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "url" }
# @raycast.argument2 { "type": "dropdown", "placeholder": "start", "optional": true, "data": [{ "title": "Cold start", "value": "cold" }, { "title": "Warm start", "value": "warm" }] }
# Launches Chrome in a fresh temp profile with --trace-config-file, records the
# first startup_duration seconds (GPU/compositor/frame categories DevTools
# hides by default), saves the trace to ~/Downloads and opens it in
# ui.perfetto.dev. Also writes a markdown report for AI agents next to the
# trace and copies it to the clipboard (see trace-report.py). Cold start (the
# default) wipes the Metal shader cache first so shaders compile from scratch.

URL="$1"
START="${2:-cold}"
DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$DIR/trace-config.json"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Raycast runs scripts with a minimal PATH; node is needed to open Perfetto
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ -z "$URL" ]; then
  echo "Missing URL"
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "Missing $CONFIG"
  exit 1
fi

if [[ ! "$START" =~ ^(cold|warm)$ ]]; then
  echo "Start must be cold or warm"
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

# 1. Cold start: wipe the Metal shader caches for Chrome's GPU helper so the
#    page's shaders compile from scratch. Your regular Chrome can stay open as
#    long as the page isn't open in it (it would refill the cache with the same
#    shaders). Chrome's own GPU caches live in the profile, which is always fresh.
if [ "$START" = "cold" ]; then
  METAL_CACHE="$(getconf DARWIN_USER_CACHE_DIR)/com.google.Chrome.helper"
  rm -rf "$METAL_CACHE/com.apple.metal" "$METAL_CACHE/com.apple.metalfe"
fi

# 2. Copy the checked-in config so each run gets its own output file
#    without touching the original
WORKDIR="$(mktemp -d)"
RUN_CONFIG="$WORKDIR/trace-config.json"
RESULT="$HOME/Downloads/gpu-trace-$(date +%Y-%m-%d-%H%M%S).json"

cp "$CONFIG" "$RUN_CONFIG"
plutil -replace result_file -string "$RESULT" "$RUN_CONFIG"
DURATION="$(plutil -extract startup_duration raw "$RUN_CONFIG")"

# 3. Launch Chrome in a fresh profile with the config. A new --user-data-dir
#    means a new browser process, so startup tracing applies even with your
#    regular Chrome open. Port 0 lets Chrome pick a free
#    debugging port and write it to DevToolsActivePort.
"$CHROME" \
  --user-data-dir="$WORKDIR/profile" \
  --remote-debugging-port=0 \
  --no-first-run \
  --no-default-browser-check \
  --trace-config-file="$RUN_CONFIG" \
  --trace-startup-format=json \
  "$URL" >/dev/null 2>&1 &
CHROME_PID=$!

# 4. Chrome writes the file once startup_duration elapses. Wait for it to
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

# 5. Open the trace in ui.perfetto.dev in the same Chrome instance: open a
#    tab over CDP and set Perfetto's "Open trace file" input to the trace
PORT="$(head -1 "$WORKDIR/profile/DevToolsActivePort" 2>/dev/null)"
if [ -n "$PORT" ] && command -v node >/dev/null &&
  node --input-type=module - "$PORT" "$RESULT" <<'EOF'
const [port, file] = process.argv.slice(2);
const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();

const ws = new WebSocket(webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });

let nextId = 0;
const pending = new Map();
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  pending.get(msg.id)?.(msg);
  pending.delete(msg.id);
};
const send = (method, params = {}, sessionId) => new Promise((resolve) => {
  const id = ++nextId;
  pending.set(id, resolve);
  ws.send(JSON.stringify({ id, method, params, sessionId }));
});

const { result: { targetId } } = await send('Target.createTarget', { url: 'https://ui.perfetto.dev' });
const { result: { sessionId } } = await send('Target.attachToTarget', { targetId, flatten: true });

// The file input exists once the Perfetto app has booted. DOM calls can fail
// while the page is still navigating, so treat errors as "not yet".
for (let i = 0; i < 80; i++) {
  const doc = await send('DOM.getDocument', { depth: 0 }, sessionId);
  const { result } = doc.result
    ? await send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: 'input[type=file]' }, sessionId)
    : {};
  if (result?.nodeId) {
    const res = await send('DOM.setFileInputFiles', { nodeId: result.nodeId, files: [file] }, sessionId);
    if (!res.error) {
      await send('Target.activateTarget', { targetId });
      process.exit(0);
    }
  }
  await new Promise((r) => setTimeout(r, 250));
}
process.exit(1);
EOF
then
  # Bring this Chrome instance (not your regular one) to the front
  osascript -e "tell application \"System Events\" to set frontmost of (first process whose unix id is $CHROME_PID) to true" >/dev/null 2>&1
  MESSAGE="Opened ${DURATION}s $START trace in Perfetto"
else
  open -R "$RESULT"
  MESSAGE="Saved ${DURATION}s $START trace"
fi

# 6. Summarize the trace into a markdown report for AI agents
if /usr/bin/python3 "$DIR/trace-report.py" "$RESULT" >/dev/null 2>&1; then
  echo "$MESSAGE · report copied to clipboard (${RESULT%.json}.md)"
else
  echo "$MESSAGE ($RESULT) · report failed"
fi
