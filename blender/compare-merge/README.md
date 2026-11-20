# Compare & Merge

Blender extension for diffing two versions of a `.blend` and merging the changes you pick, git-style. Built for the "artist's copy vs. final file" flow: open the final file, point the tool at the artist's version, look at every difference, preview either version of anything in the viewport and merge only what you want.

It compares everything a glTF export is made of: objects (transforms, parenting, modifiers, constraints, material slots, collections, custom properties/extras), meshes (geometry, topology, UV maps, attributes, shape keys, weights), materials and node groups (node by node, every input, every link), images (file contents, packed data, color space), cameras, lights, actions (every keyframe), armatures, curves/text, worlds, collections and scene settings (camera, frame range, fps).

## Install

Install it from the JOYCO extensions repository so Blender tells you about new versions: add `https://joyco-studio.github.io/scripts/index.json` as a remote repository (see [Install and get updates](../README.md#install-and-get-updates)), then search for it in Preferences > Get Extensions (Blender 5.1+, tested on 5.3).

Or download `compare_merge-<version>.zip` from a `compare-merge@…` [GitHub Release](https://github.com/joyco-studio/scripts/releases) and install it with Preferences > Get Extensions > ⌄ > Install from Disk (no update notifications).

For development, symlink the folder instead:

```sh
ln -s "$PWD/compare_merge" ~/Library/Application\ Support/Blender/5.3/extensions/user_default/compare_merge
```

## Use it

1. Open the final file and go to the **Compare & Merge** tab (added next to Layout; also File > Compare & Merge…).
2. Pick the other `.blend` and press **Compare**. The sidebar lists every difference:

   | | Meaning | Choice |
   |---|---|---|
   | ✎ | different in the two files | Current / Incoming |
   | Az | same content, renamed | Current / Incoming |
   | + | only in the incoming file | Skip / Add |
   | − | only in this file | Keep / Remove |

   Click a row (or an object in the viewport) to see the changed fields side by side. Clicking a row also selects what it affects and reveals it in the Outliner. Meshes also show how many vertices moved and by how much. Above the list, filter by type, by name (search field) and by kind of change (one toggle each for changed, renamed, new, missing and identical, with counts; identical is off by default). **All Current / All Incoming** apply only to the rows listed.
3. Pick versions. The viewport always shows the **Merge Result**: what the file will look like with your picks. Picking Incoming on an object also picks its changed mesh, materials, images and animation; a new object brings its new children and the new objects it needs (parent, modifier targets), and a scene brings its new camera and world. Set any of them back by hand: a new object set to Skip stays out even if something else uses it.
4. Look at the selected row's other version:
   - **Show: Current / Incoming** flips the whole scene between the two files.
   - **Ghost** overlays the other version as a wireframe.
   - **Blink** alternates between the two versions (rate adjustable).
   - **Solo** hides everything the row doesn't affect and frames it (each row you pick while Solo is on is framed too, like View Selected). Switching Show or the row's version keeps the view still so you see the change in place, and only re-frames when the version now shown is out of view; **Frame** zooms to it; **Flip** switches the row.
5. Check the **Merge** panel: everything the merge will do, grouped into Update, Rename, Add and Remove, with a count per type and what changed on each row (click one to select it in the list). Then **Merge into This File**. Ctrl+Z undoes the whole merge and brings your picks back.

## The preview never touches the file

Blender doesn't record when anything changed, so the tool can't tell which version is newer; you decide by looking. Looking has to be safe:

- The incoming file is linked into the session, read-only, and every data-block that brings is flagged `is_runtime_data`, which Blender never writes to disk.
- The preview is a separate runtime scene ("Compare & Merge Preview") that shares the real scene's collections. Objects shown in another version are hidden only in the preview's view layer and replaced by runtime copies. The real objects, meshes and materials are never edited.
- Saving (Ctrl+S, autosave, Save As) mid-preview writes only your real data; windows are pointed back at the real scene for the save. Closing Blender or opening another file just drops the preview. Linked scenes are the one thing Blender would still write, so the incoming scenes are read once and unlinked.
- Leaving the tab shows the real scene again. Edits you make there are picked up when you come back.
- Only **Merge** changes data. It appends the picked data-blocks, swaps them in everywhere the current ones were used (`user_remap`), drops duplicates of anything you kept, restores names and collection membership and cleans up leftovers. No `.001` copies are left behind.

## Command line (people, agents, CI)

```sh
./blend-diff final.blend artist.blend              # git-style summary of every difference
./blend-diff final.blend artist.blend --json        # machine-readable
./blend-diff final.blend artist.blend --all --limit 50
```

Exit code: 0 identical, 1 error, 2 the files differ (like `git diff --exit-code`). Runs Blender headless with `--factory-startup` and this checkout's add-on; set `BLENDER` to choose the executable.

## Limits

- Two-way diff: without a common ancestor the tool can't tell which side changed something, only that it differs. A three-way mode (pick a base file, auto-take one-sided changes, flag conflicts) is the natural next step.
- Entities are matched by name; renames are detected only when the content is identical.
- Data linked into either file from other libraries is not compared.
- The preview swaps picked images and node groups inside materials and Geometry Nodes modifiers, but not images or groups nested inside other node groups.
- Vertex weights are compared on meshes up to 100k vertices (no bulk API for them).

## Files

| File | Contents |
|---|---|
| `snapshot.py` | per-type fingerprints: every field as a readable string, heavy data as content hashes |
| `diff.py` | matching, rename detection, field diffs (pure Python) |
| `session.py` | linking the incoming file as runtime data, lookups, what can be picked |
| `preview.py` | the runtime preview scene, derived from the picks; Ghost / Blink / Solo |
| `merge.py` | append, reconcile, restore names and membership |
| `api.py` | one function per action, shared by the sidebar, the CLI and the tests |
| `ui.py` | workspace tab, panels, list, operators |
| `blend-diff`, `blend_diff.py` | command line |
| `tests/run` | headless tests (diff, preview isolation, save mid-preview, merge, reopen) |
