# Raycast Scripts

Scripts meant to be used with [Raycast](https://raycast.com/) for quick workflow integration.

## Installation

1. Open Raycast → Settings → Extensions → Add
2. Click "Add Script Directory"

   ![add-script-directory](https://qfxa88yauvyse9vr.public.blob.vercel-storage.com/add-script-directory)
3. Pull the repo and select this `raycast` folder or copy the scripts to you local folder.
4. All scripts will be automatically available in Raycast

This approach lets Raycast watch the folder, so any new scripts added here will automatically appear in your commands.

---

## Scripts

### `get-ip.sh` 🌍

Copies your local private IP address to clipboard. Useful for sharing your dev server with other devices on the network.

**Usage:** Run from Raycast, IP is automatically copied.

---

### `cold-open.sh` 🧊

Opens a URL in Chrome with a true cold load: quits Chrome, wipes the Metal shader cache for Chrome's GPU helper, and launches the URL in a fresh temporary profile (no cache, no extensions, no compiled shaders).

**Usage:** Run from Raycast and enter the URL (scheme optional). Include any query params directly in the URL.

```
> Cold Open
> Enter url: foo.vercel.app?timings
→ "Opened https://foo.vercel.app?timings (profile: /var/folders/.../tmp.XXXX)"
```

> ⚠️ Quits all open Chrome windows.

---

### `gpu-trace.sh` 🎞️

Records a startup performance trace with the GPU/compositor categories DevTools hides by default, then opens it in a viewer. It launches a separate Chrome instance in a fresh temporary profile with `--trace-config-file=trace-config.json`, records the first N seconds after launch, saves the trace to `~/Downloads`, and loads it into [Perfetto](https://ui.perfetto.dev) in that same window.

The categories and buffer mode come from [`trace-config.json`](./trace-config.json), which is checked in next to the script, so every run uses the same config. Each run writes to a timestamped copy of the config, so the original never changes.

**Requirements:** `node` (18+) to open the viewer automatically. Without it, the script just reveals the file in Finder. `python3` for the report (see [`trace-report.py`](#trace-reportpy-)).

**Usage:** Run from Raycast and enter the URL (scheme optional; `localhost` gets `http://`). Both other arguments are optional:

- **seconds** overrides `startup_duration` (default 10)
- **open in** picks the viewer: `Perfetto` (default), `DevTools` (Performance panel), or `Finder`

```
> GPU Trace
> Enter url: localhost:3000   seconds: 5   open in: Perfetto
→ "Opened 5s trace in perfetto (~/Downloads/gpu-trace-2026-11-04-191158.json)"
```

In Perfetto, expand **GPU Process** (`CrGpuMain`, `VizCompositorThread`) to see `GPUTask`, `DrawFrame`, and `Graphics.Pipeline`. The trace loads locally in the browser and isn't uploaded anywhere.

[`perfetto-queries.sql`](./perfetto-queries.sql) has ready-made queries to paste into Perfetto's **Query (SQL)** page: presented vs dropped frames, drops over time, GPU frame cost, long tasks with what each one was doing, self time in the GPU process and in your page, shader/pipeline compilation over time, and the hottest JS functions and call stacks.

After each recording, the script also writes a markdown report next to the trace (`gpu-trace-….md`) and copies it to the clipboard. Paste it into an AI agent: it has every query's results plus notes on how to read them, in a few thousand tokens instead of a 100MB+ JSON file.

---

### `trace-report.py` 🧾

Builds that agent-friendly report for any existing trace. It runs [`perfetto-queries.sql`](./perfetto-queries.sql) with Perfetto's [trace processor](https://perfetto.dev/docs/analysis/trace-processor) and writes `<trace>.md` next to the trace. It also copies the report to the clipboard.

**Requirements:** `python3`. The first run downloads Perfetto's official `trace_processor` launcher to `~/.local/share/perfetto/` (it fetches and checksums the binary), unless `trace_processor` is already on your `PATH`.

**Usage:** Run from Raycast with a trace path, or leave it empty to use the newest `~/Downloads/gpu-trace-*.json`.

```
> Trace Report
→ "Report copied to clipboard (~/Downloads/gpu-trace-2026-11-04-192550.md)"
```

Equivalent manual launch:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir=/tmp/chrome-trace \
  --trace-config-file=$PWD/trace-config.json \
  http://localhost:3000
```

> ⚠️ Recording only covers startup: it starts when Chrome launches and stops after `startup_duration`. You can't use it to trace a moment you pick later; use the DevTools Performance panel for that. Your regular Chrome stays open.

---

### `killport.sh` 💀

Kill a process running on a specific port. No more hunting for PIDs.

**Usage:** Run from Raycast, enter the port number when prompted.

```
> Kill Port
> Enter port: 3000
→ "Killed process 12345 on port 3000"
```

---

### `pull-quicklinks.sh` 🔗

Downloads the latest quicklinks.json from the Joyco registry and saves it to your Downloads folder.

**Usage:** Run from Raycast, file is automatically downloaded.

```
> Pull Quicklinks
→ "✓ Downloaded 12 quicklinks to ~/Downloads/quicklinks-pull-12-09-2024.json"
```

---

### `video-first-frame.sh` 🎬

Extracts the first frame of a video (local file or remote URL) as a PNG image. Opens a Finder dialog to let you choose where to save the extracted frame.

**Requirements:** `ffmpeg` must be installed (`brew install ffmpeg`)

**Usage:** Run from Raycast, enter the video path or URL when prompted.

```
> Extract Video First Frame
> Enter video: /path/to/video.mp4
→ Opens Finder save dialog
→ "Saved to first_frame.png"
```

Works with:
- Local video files (e.g., `/Users/you/Movies/clip.mp4`)
- Remote URLs (e.g., `https://example.com/video.mp4`)
