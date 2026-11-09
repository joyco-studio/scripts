# Blender Add-ons

Custom Blender plugins for exporting data to web-friendly formats.

## Add-ons

| Add-on | Description |
|--------|-------------|
| [bezier-exporter](./bezier-exporter/) | Export Bezier curves to JSON for Three.js |
| [texel-coverage](./texel-coverage/) | Size textures, lay out UVs and cull geometry from the cameras that see them; optimizes glTF exports |

## Versions and releases

Each add-on is a private workspace package (`blender/<addon>/package.json`); its `version` is the source of truth and the release flow is the same Changesets flow as the CLI:

1. In your PR, run `pnpm changeset`, pick the add-on and patch / minor / major, and describe the change.
2. When it reaches `main`, the Release workflow opens a "Version Packages" PR that bumps the version, writes the add-on's `CHANGELOG.md` and copies the number into `blender_manifest.toml` (or `bl_info` for single-file add-ons).
3. Merging that PR tags `<addon>@<version>`, creates a GitHub Release and attaches the installable file (`texel_coverage-0.3.0.zip`, `BezierExporter-1.0.0.py`).

Install a release from the repo's GitHub Releases page with Preferences > Get Extensions > ⌄ > Install from Disk (legacy `.py` add-ons: Preferences > Add-ons > Install from Disk).

Locally: `pnpm blender:build` writes the same files to `dist/blender/`, and `pnpm blender:check` (also run in CI) fails if a manifest and its `package.json` disagree. Never edit the manifest version by hand.

