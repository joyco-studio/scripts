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

Records a startup performance trace with the GPU/compositor categories DevTools hides by default. It launches a separate Chrome instance in a fresh temporary profile with `--trace-config-file=trace-config.json`, records the first N seconds after launch, and saves the trace to `~/Downloads`. Open the file in DevTools → Performance → Load profile.

The categories and buffer mode come from [`trace-config.json`](./trace-config.json), which is checked in next to the script, so every run uses the same config. Each run writes to a timestamped copy of the config, so the original never changes.

**Usage:** Run from Raycast and enter the URL (scheme optional; `localhost` gets `http://`). The seconds argument is optional and overrides `startup_duration` (default 10).

```
> GPU Trace
> Enter url: localhost:3000   seconds: 5
→ "Saved 5s trace to ~/Downloads/gpu-trace-2026-11-04-191158.json"
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
