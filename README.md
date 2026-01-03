# Scripts

A collection of utility scripts organized by usage pattern.

Note: the legacy `public/` HTTP-linkable scripts are deprecated and removed. Use the pnpx CLI in `packages/cli` instead.

## 📁 Repository Structure

| Folder | Description | Usage |
|--------|-------------|-------|
| [`/packages/cli`](./packages/cli/) | Joyco scripts CLI | Run via pnpx |
| [`/raycast`](./raycast/) | macOS workflow automation | Copy-paste into Raycast |
| [`/blender`](./blender/) | 3D export plugins | Install as Blender add-ons |

---

## Quick Links

### [`/packages/cli`](./packages/cli/) — pnpx CLI

Bundled CLI for Joyco utility scripts.

- **`joyco-scripts`** — Run scripts via `pnpx @joycostudio/scripts`

→ [View documentation](./packages/cli/)

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
