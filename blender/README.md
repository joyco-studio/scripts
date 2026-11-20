# Blender Add-ons

Custom Blender plugins for exporting data to web-friendly formats and managing the files they come from.

## Add-ons

| Add-on | Description |
|--------|-------------|
| [bezier-exporter](./bezier-exporter/) | Export Bezier curves to JSON for Three.js |
| [compare-merge](./compare-merge/) | Diff two versions of a .blend, preview either version of every change and merge the ones you pick |
| [texel-coverage](./texel-coverage/) | Size textures, lay out UVs and cull geometry from the cameras that see them; optimizes glTF exports |

## Install and get updates

[![Extensions repository](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fjoyco-studio.github.io%2Fscripts%2Findex.json&query=%24.data.length&label=Blender%20extensions&color=e87d0d)](https://joyco-studio.github.io/scripts/)
[![compare-merge](https://img.shields.io/github/v/release/joyco-studio/scripts?filter=compare-merge%40*&label=compare-merge)](https://github.com/joyco-studio/scripts/releases?q=compare-merge)
[![texel-coverage](https://img.shields.io/github/v/release/joyco-studio/scripts?filter=texel-coverage%40*&label=texel-coverage)](https://github.com/joyco-studio/scripts/releases?q=texel-coverage)

The extensions (`compare-merge`, `texel-coverage`) are published to our own extensions repository, so Blender 4.2+ can install them and offer updates:

```
https://joyco-studio.github.io/scripts/index.json
```

1. In Blender, open Edit > Preferences > Get Extensions.
2. Open the Repositories menu (⌄ at the top right), choose **Add Remote Repository** and paste the URL above.
3. Turn on **Check for Updates on Startup**.
4. Search for the extension in Get Extensions and click Install. Blender lists new versions under Updates; install them from there.

[joyco-studio.github.io/scripts](https://joyco-studio.github.io/scripts/) lists what the repository has. Older versions stay on the [GitHub Releases](https://github.com/joyco-studio/scripts/releases) page and in the repository's `archive/` folder. `bezier-exporter` is a legacy single-file add-on: install its `.py` from GitHub Releases with Preferences > Add-ons > Install from Disk.

## Versions and releases

Each add-on is a private workspace package (`blender/<addon>/package.json`); its `version` is the source of truth and the release flow is the same Changesets flow as the CLI:

1. In your PR, run `pnpm changeset`, pick the add-on and patch / minor / major, and describe the change.
2. When it reaches `main`, the Release workflow opens a "Version Packages" PR that bumps the version, writes the add-on's `CHANGELOG.md` and copies the number into `blender_manifest.toml` (or `bl_info` for single-file add-ons).
3. Merging that PR tags `<addon>@<version>` and creates a GitHub Release. The [Publish Blender extensions](../.github/workflows/publish-extension.yml) workflow then, with Blender 5.2 LTS:
   - fails unless the tag, `package.json` and `blender_manifest.toml` / `bl_info` name the same version, and runs `blender --command extension validate`;
   - builds with `blender --command extension build` and attaches the installable file to the release (`texel_coverage-0.3.0.zip`, `BezierExporter-1.0.0.py`);
   - adds the zip to the `gh-pages` branch (the newest version of each extension next to `index.json`, older ones in `archive/`), regenerates `index.json` and `index.html` with `blender --command extension server-generate --html` and pushes. Blender picks the new version up from there.

To republish, or to publish a release made by hand, run the workflow from the Actions tab with the tags (`texel-coverage@0.3.0 compare-merge@0.2.0`); publishing a release by hand in the GitHub UI runs it too.

Locally: `pnpm blender:build` writes the same files to `dist/blender/` (with Blender on `PATH` or in `$BLENDER` it uses the official build, otherwise plain zip), `pnpm blender:validate` also runs Blender's manifest validation, and `pnpm blender:check` (also run in CI) fails if a manifest and its `package.json` disagree. Never edit the manifest version by hand.

