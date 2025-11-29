# Scripts

A collection of utility scripts organized by usage pattern.

## 📁 Repository Structure

| Folder | Description | Usage |
|--------|-------------|-------|
| [`/public`](./public/) | Image processing scripts | Curl & run via HTTP |
| [`/raycast`](./raycast/) | macOS workflow automation | Copy-paste into Raycast |
| [`/blender`](./blender/) | 3D export plugins | Install as Blender add-ons |

---

## Quick Links

### [`/public`](./public/) — HTTP-Linkable Scripts

Scripts fetched and executed directly via HTTP. Add to `package.json` without cluttering your project.

- **`compress.py`** — Batch compress images to WebP
- **`rename.py`** — Sequential file renaming for image sequences
- **`resize.py`** — Batch resize by dimensions or scale

→ [View documentation](./public/)

---

### [`/raycast`](./raycast/) — Raycast Scripts

Copy-paste scripts for [Raycast](https://raycast.com/) workflow integration.

- **`get-ip.sh`** 🌍 — Copy local IP to clipboard
- **`killport.sh`** 💀 — Kill process on a port

→ [View documentation](./raycast/)

---

### [`/blender`](./blender/) — Blender Add-ons

Custom plugins for exporting Blender data to web-friendly formats.

- **`bezier-exporter`** — Export Bezier curves to JSON for Three.js

→ [View documentation](./blender/)
