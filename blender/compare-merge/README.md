# Compare & Merge

Blender extension for diffing two versions of a `.blend` and merging the changes you pick, git-style. Built for the "artist's copy vs. final file" flow: open the final file, point the tool at the artist's version, look at every difference, preview either version of anything in the viewport and merge only what you want.

It compares everything a glTF export is made of: objects (transforms, parenting, modifiers and their Geometry Nodes inputs, constraints, material slots, collections, custom properties/extras), meshes (geometry, topology, UV maps, attributes, shape keys, weights), materials and node groups (node by node, every input and setting, every link), images (file name and contents, including every UDIM tile and sequence frame, packed data, color space), cameras, lights, actions (every keyframe), armatures, curves/text, worlds, collections and scene settings (camera, frame range, fps).

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
4. Or take only some fields of a changed row: the **Details** panel groups its changed fields into units, each with its own **Current / Incoming**. Take their UV map and keep your vertex positions, their location and keep your material slot, their roughness and keep your base color. The row then shows **Mixed**; pressing its Current or Incoming icon takes the whole version again. A unit is the smallest set of fields that still gives a valid data-block:

   | Type | Units |
   |---|---|
   | Object | transform, visibility, parent (with its offset), collections, each material slot, each modifier's / constraint's settings, animation, pose, instancing, data, each custom property |
   | Mesh | positions (with the Basis shape key), each UV map, each attribute, each shape key, materials, each custom property |
   | Material, World | each setting, each node input or node setting, each custom property |
   | Light, Camera | each setting, each custom property |

   Mesh units are on the mesh row, so they change every object using that mesh (the panel says so when there are several). Some units only come with the whole row and are shown locked, with the reason (see Limits). Picking every unit is the same as Incoming.
5. Look at the selected row's other version:
   - **Show: Current / Incoming** flips the whole scene between the two files.
   - **Ghost** overlays the other version as a wireframe (for a Mixed row: this file's version).
   - **Blink** alternates between the two versions (rate adjustable).
   - **Solo** hides everything the row doesn't affect and frames it (each row you pick while Solo is on is framed too, like View Selected). Switching Show or the row's version keeps the view still so you see the change in place, and only re-frames when the version now shown is out of view; **Frame** zooms to it; **Flip** switches the row.
6. Check the **Merge** panel: everything the merge will do, grouped into Update, Rename, Add and Remove, with a count per type and what changed on each row, or for a Mixed row what comes from where (click one to select it in the list). Then **Merge into This File**. Ctrl+Z undoes the whole merge and brings your picks back.

## The preview never touches the file

Blender doesn't record when anything changed, so the tool can't tell which version is newer; you decide by looking. Looking has to be safe:

- The incoming file is linked into the session, read-only, and every data-block that brings is flagged `is_runtime_data`, which Blender never writes to disk.
- The preview is a separate runtime scene ("Compare & Merge Preview") that shares the real scene's collections. Objects shown in another version are hidden only in the preview's view layer and replaced by runtime copies. The real objects, meshes and materials are never edited.
- Saving (Ctrl+S, autosave, Save As) mid-preview writes only your real data; windows are pointed back at the real scene for the save. Closing Blender or opening another file just drops the preview. Linked scenes are the one thing Blender would still write, so the incoming scenes are read once and unlinked.
- Leaving the tab shows the real scene again. Edits you make there are picked up when you come back.
- Only **Merge** changes data. It appends the picked data-blocks, swaps them in everywhere the current ones were used (`user_remap`), drops duplicates of anything you kept, restores names and collection membership and cleans up leftovers. No `.001` copies are left behind.
- A Mixed row is previewed as a runtime copy of your data-block with the picked units copied in, by the same functions Merge uses (`apply.py`). Merge copies them onto your data-block itself, which keeps its name and every user.

## Command line (people, agents, CI)

```sh
./blend-diff final.blend artist.blend              # git-style summary of every difference
./blend-diff final.blend artist.blend --json        # machine-readable
./blend-diff final.blend artist.blend --all --limit 50
```

Exit code: 0 identical, 1 error, 2 the files differ (like `git diff --exit-code`). Runs Blender headless with `--factory-startup` and this checkout's add-on; set `BLENDER` to choose the executable.

## Limits

- Two-way diff: without a common ancestor the tool can't tell which side changed something, only that it differs. A three-way mode (pick a base file, auto-take one-sided changes, flag conflicts) is the natural next step.
- Field picks need the same structure on both sides. Whole row only: a mesh whose topology differs (vertex, edge or face count, or vertex order: UVs and positions only fit their own geometry), vertex weights and vertex groups, a material or world whose node wiring changed (links, sockets, nodes added or removed), an object whose modifier or constraint stack changed (added, removed, reordered or retyped; their other units stay pickable), drivers, list values (color ramp stops, constraint targets…), a node input that only one side's node settings show, and object type changes. Images, actions, node groups, armatures, curves, collections and scene settings are always whole rows.
- Node inputs and settings are picked one by one rather than per node: with the wiring unchanged, any combination is a valid tree.
- Entities are matched by name; renames are detected only when the content is identical. What refers to a renamed data-block (material slots, a mesh's materials, a collection's objects…) reads it under its current name, so a rename is one row, not one per user.
- Images are compared by file name and content, not folder: the two files can sit in different folders and reach the same texture by different relative paths, or each have their own copy of it.
- Data linked into either file from other libraries is not compared.
- The preview swaps picked images and node groups inside materials and Geometry Nodes modifiers, but not images or groups nested inside other node groups.
- Vertex weights are compared on meshes up to 100k vertices (no bulk API for them).
- Open issues found on a production file (NLA, collections, vertex group renames…) are in [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
- A keyframed or driven value shows as `animated` rather than its number: Blender writes the current frame's value onto the open file, so the number would only say where the playhead is. Changes to the animation show on the action (or the driver).

## Files

| File | Contents |
|---|---|
| `snapshot.py` | per-type fingerprints: every field as a readable string, heavy data as content hashes |
| `diff.py` | matching, rename detection, field diffs (pure Python) |
| `session.py` | linking the incoming file as runtime data, lookups, what can be picked |
| `preview.py` | the runtime preview scene, derived from the picks; Ghost / Blink / Solo |
| `merge.py` | append, reconcile, restore names and membership |
| `apply.py` | field-level picks: units (and why some are locked) and one copy function per unit, shared by preview and merge |
| `api.py` | one function per action, shared by the sidebar, the CLI and the tests |
| `ui.py` | workspace tab, panels, list, operators |
| `blend-diff`, `blend_diff.py` | command line |
| `tests/run` | headless tests (diff, preview isolation, save mid-preview, merge, field picks, reopen) |
