# Texel Coverage

Blender extension that uses the scene's cameras to decide how big every texture must be, where UV space should go and which faces nobody ever sees. Artists keep high-resolution files; culling and texture downsizing only happen inside the exported GLB.

Full guide with screenshots: [Texel Coverage in Notion](https://app.notion.com/p/40fee5fdb1b58183a4bbd64c38e93260).

## Install

Download `texel_coverage-<version>.zip` from the latest `texel-coverage@…` [GitHub Release](https://github.com/joyco-studio/scripts/releases) and install it with Preferences > Get Extensions > ⌄ > Install from Disk (Blender 4.2+, tested on 5.1). The installed version shows in Preferences and in every CLI result and export report (`meta.version`).

For development, symlink the folder instead:

```sh
ln -s "$PWD/texel_coverage" ~/Library/Application\ Support/Blender/5.1/extensions/user_default/texel_coverage
```

then enable it in Preferences > Add-ons. Releases and changelogs: see [Versions and releases](../README.md#versions-and-releases).

## What's where

| Panel | Use it to |
|---|---|
| 3D View > Sidebar > Texel | Sync site cameras, Analyze (Ratio / Required views), Suggest UVs, preview and verify the cull, export settings |
| 3D View > Sidebar > Texel > UVs | Suggest UVs, then **Bake to Suggested UVs**: bakes every texture into the new layout (`<image>_tc`, saved next to the original or packed), promotes it and switches the materials. Originals are untouched; Ctrl+Z or **Revert Bake** goes back |
| UV Editor > Sidebar > Texel | Ratio overlay while editing, lock islands, select proposed cuts, promote |
| File > Export > glTF 2.0 > Texel Optimize | Turn optimize-on-export on or off |

## How the export works

The extension hooks the stock glTF exporter (`glTF2_pre_export_callback` / `glTF2_post_export_callback`). With **Optimize glTF Exports** on, every export:

1. refreshes the stored analysis and cull if anything they depend on changed (fingerprint of meshes, modifiers, transforms, animation, cameras and settings),
2. swaps each texture for a copy at the size the cameras need with the current UVs (never upscales),
3. hides helper UV maps (`_tc_suggest`, `_tc_backup`), enables the "TC Cull Unseen" modifiers and adds a fixed-rule triangulation,
4. lets the exporter run (Apply Modifiers forced on),
5. restores everything and writes `<name>.texel.md` / `.texel.json` next to the GLB.

**Compress with Chopper** (Export section and the glTF dialog) also writes `<name>.compressed.glb` with chopper, the compressor the site pipeline uses, so you see the final size. Point **Chopper** at a project with chopper installed (`node_modules/.bin/chopper`, e.g. the website repo); it runs in the background after the GLB is written and the sizes show in the panel and the report. The command line uses the same setting, or `--chopper PATH` for one run.

Temporary images and stashed UV maps are tagged, so **Restore** (or reopening the file) recovers from an export that crashed halfway.

## Command line (people and agents)

```sh
./texel scene.blend status                     # fresh/stale, scene summary
./texel scene.blend update                     # analyze + cull, only if out of date
./texel scene.blend analyze cull --save        # recompute and store in the .blend
./texel scene.blend suggest --objects SKATE,PILES --save
./texel scene.blend verify --max-diff-px 2     # exit 2 if the cull changes more pixels
./texel scene.blend export --out build/model.glb --json
./texel scene.blend export --out build/model.glb --chopper ../website   # + compressed GLB
./texel --help
```

- Commands run in order in one Blender session: `status settings sync-cameras analyze cull update suggest promote plan verify export restore`.
- Settings come from the .blend (what artists set in the sidebar). `--set key=value` overrides one for this run; `--save` writes the file back.
- stdout carries only the result: a readable summary with the same reports the sidebar writes, or JSON with `--json` (`{"ok", "exit_code", "results": [{"command", "ok", "message", "data", "warnings", "meta"}]}`). `data` is deterministic; times and settings live in `meta`. Blender's log goes to stderr.
- Exit codes: 0 ok, 1 error, 2 a check failed (`verify --max-diff-px`, `status --check`).
- Runs Blender headless with `--factory-startup` and this checkout's add-on, so results don't depend on anyone's preferences. Set `BLENDER` to choose the executable.

The sidebar buttons and the command line call the same functions (`texel_coverage/api.py`). A GLB exported from the File menu and one from `./texel ... export` are byte-identical; export triangulates with a fixed rule (a temporary modifier) because Blender's UI and headless modes can split near-flat quads differently.

## Files

| File | Contents |
|---|---|
| `core.py` | measurement, attributes, report, cache fingerprints |
| `cull.py` | visibility with margins, pixel-exact face-ID pass, modifiers, verify |
| `export.py` | texture plan, image swap, helper-UV stash, exporter callbacks, report |
| `uv_suggest.py` | deterministic suggested layout, locks, proposed cuts, promote |
| `overlay.py` | UV editor ratio overlay and readout, target checker |
| `api.py` | one function per action, shared by the buttons and the command line |
| `ui.py` | properties, operators, panels |
| `texel`, `texel_cli.py` | command line wrapper and entry point |

Site camera rigs live with each site. The JOYCO website's is `scripts/texel-coverage/hero_cameras.py` in the website repo.
