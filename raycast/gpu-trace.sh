#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title GPU Trace
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🎞️
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "url" }
# @raycast.argument2 { "type": "text", "placeholder": "seconds (10)", "optional": true }
# @raycast.argument3 { "type": "dropdown", "placeholder": "open in", "optional": true, "data": [{ "title": "Perfetto", "value": "perfetto" }, { "title": "DevTools", "value": "devtools" }, { "title": "Finder", "value": "finder" }] }
# Launches Chrome in a fresh temp profile with --trace-config-file, records the
# first N seconds (GPU/compositor/frame categories DevTools hides by default),
# saves the trace to ~/Downloads and opens it in ui.perfetto.dev (or DevTools
# → Performance, or Finder). Also writes a markdown report for AI agents next
# to the trace and copies it to the clipboard (see trace-report.py).

URL="$1"
DURATION="$2"
OPENER="${3:-perfetto}"
DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$DIR/trace-config.json"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Raycast runs scripts with a minimal PATH; node is needed to open the viewer
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ -z "$URL" ]; then
  echo "Missing URL"
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "Missing $CONFIG"
  exit 1
fi

if [ -n "$DURATION" ] && [[ ! "$DURATION" =~ ^[0-9]+$ ]]; then
  echo "Seconds must be a whole number"
  exit 1
fi

if [[ ! "$OPENER" =~ ^(perfetto|devtools|finder)$ ]]; then
  echo "Open in must be perfetto, devtools or finder"
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

# 1. Copy the checked-in config so each run gets its own output file
#    (and an optional duration override) without touching the original
WORKDIR="$(mktemp -d)"
RUN_CONFIG="$WORKDIR/trace-config.json"
RESULT="$HOME/Downloads/gpu-trace-$(date +%Y-%m-%d-%H%M%S).json"

cp "$CONFIG" "$RUN_CONFIG"
plutil -replace result_file -string "$RESULT" "$RUN_CONFIG"
if [ -n "$DURATION" ]; then
  plutil -replace startup_duration -integer "$DURATION" "$RUN_CONFIG"
fi
DURATION="$(plutil -extract startup_duration raw "$RUN_CONFIG")"

# 2. Launch a separate Chrome instance in a fresh profile with the config.
#    A new --user-data-dir means a new browser process, so startup tracing
#    applies even if your regular Chrome is already open. Port 0 lets Chrome
#    pick a free debugging port and write it to DevToolsActivePort.
"$CHROME" \
  --user-data-dir="$WORKDIR/profile" \
  --remote-debugging-port=0 \
  --no-first-run \
  --no-default-browser-check \
  --trace-config-file="$RUN_CONFIG" \
  --trace-startup-format=json \
  "$URL" >/dev/null 2>&1 &
CHROME_PID=$!

# 3. Chrome writes the file once startup_duration elapses. Wait for it to
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

# 4. Open the trace in the same Chrome instance over CDP:
#    - perfetto: open ui.perfetto.dev and set its file input to the trace
#    - devtools: Chrome blocks devtools:// URLs with loadTimelineFromURL, so
#      open a DevTools tab and call the Performance panel's loadFromURL()
PORT="$(head -1 "$WORKDIR/profile/DevToolsActivePort" 2>/dev/null)"
if [ "$OPENER" != "finder" ] && [ -n "$PORT" ] && command -v node >/dev/null &&
  node --input-type=module - "$PORT" "$RESULT" "$OPENER" <<'EOF'
import { pathToFileURL } from 'node:url';

const [port, file, opener] = process.argv.slice(2);
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const viewers = {
  perfetto: {
    url: 'https://ui.perfetto.dev',
    // Perfetto's "Open trace file" input exists once the app has booted
    async load(sessionId) {
      const { result: { root } } = await send('DOM.getDocument', { depth: 0 }, sessionId);
      const { result } = await send('DOM.querySelector', { nodeId: root.nodeId, selector: 'input[type=file]' }, sessionId);
      if (!result?.nodeId) return false;
      const res = await send('DOM.setFileInputFiles', { nodeId: result.nodeId, files: [file] }, sessionId);
      return !res.error;
    },
  },
  devtools: {
    url: 'devtools://devtools/bundled/devtools_app.html?panel=timeline',
    // The DevTools modules aren't ready right after the tab opens
    async load(sessionId) {
      const expression = `(async () => {
        const UI = await import('devtools://devtools/bundled/ui/legacy/legacy.js');
        const Timeline = await import('devtools://devtools/bundled/panels/timeline/timeline.js');
        await UI.ViewManager.ViewManager.instance().showView('timeline');
        Timeline.TimelinePanel.TimelinePanel.instance().loadFromURL(${JSON.stringify(pathToFileURL(file).href)});
        return true;
      })()`;
      const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
      return res.result?.result?.value === true;
    },
  },
};

const viewer = viewers[opener];
const { result: { targetId } } = await send('Target.createTarget', { url: viewer.url });
const { result: { sessionId } } = await send('Target.attachToTarget', { targetId, flatten: true });

for (let i = 0; i < 80; i++) {
  if (await viewer.load(sessionId)) {
    await send('Target.activateTarget', { targetId });
    process.exit(0);
  }
  await sleep(250);
}
process.exit(1);
EOF
then
  # Bring this Chrome instance (not your regular one) to the front
  osascript -e "tell application \"System Events\" to set frontmost of (first process whose unix id is $CHROME_PID) to true" >/dev/null 2>&1
  MESSAGE="Opened ${DURATION}s trace in $OPENER"
else
  open -R "$RESULT"
  MESSAGE="Saved ${DURATION}s trace"
fi

# 5. Summarize the trace into a markdown report for AI agents
if /usr/bin/python3 "$DIR/trace-report.py" "$RESULT" >/dev/null 2>&1; then
  echo "$MESSAGE · report copied to clipboard (${RESULT%.json}.md)"
else
  echo "$MESSAGE ($RESULT) · report failed"
fi
