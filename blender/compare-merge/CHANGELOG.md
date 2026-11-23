# compare-merge

## 0.3.1

### Patch Changes

- 4c70a87: Follow-up fixes to comparing what the files hold:

  - Packed images are compared again. Since the last release every packed image failed to read ("could not read"), so two different packed textures looked identical.
  - Only the keyframed components of a value read `animated`: with just location X keyed, a change to Y or Z still shows, as `(animated, 1, 0)`.
  - Drivers show on every row, not only on objects: their expression, variables and targets are compared for lights, cameras, materials and worlds (also drivers inside the node tree), meshes (shape key drivers) and the rest. The action of a material's node tree or of a mesh's shape keys is listed too. These come with the whole row when merging.
  - A muted driver, keyframe curve or NLA strip no longer hides the value it would animate: the saved value is compared, and muting or unmuting a driver shows as a driver change.

- fc37002: The diff now reads only what the files hold, not what this Blender session did to the open one:

  - Images no longer show as changed when only their pixel size differs as "1024×1024 → —". The size was only known for images Blender had loaded (ones the viewport drew). The content hash still catches real pixel changes, including a resize.
  - Keyframed and driven values (object transforms, pose bones, node inputs, light and camera settings, shape key values, custom properties) show as `animated` instead of the value at the playhead, which made every animated object look changed after scrubbing the timeline. Changes to the animation show on the action.
  - Meshes in Edit Mode are compared with their unsaved edits. Before, a moved vertex was missed and the mesh showed fake topology and attribute changes.
  - UDIM images hash every tile and image sequences every frame. Before, UDIM images had no content hash at all and sequences hashed only their first frame, so a repainted tile or frame went unnoticed.

## 0.3.0

### Minor Changes

- 4e848ff: Field-level picks: take some fields of a changed row from the incoming file and keep the rest, e.g. their UV map with your vertex positions on the same mesh. The Details panel groups the changed fields into units (transform, parent, a material slot, a UV map, positions, a shape key, a node input, a light's energy…), each with its own Current / Incoming; the row shows as Mixed. The preview shows the mix and Merge copies the picked units onto your data-block, which keeps its name and every user. Meshes whose topology differs, node wiring changes and modifier stack changes can only be taken whole.
- e16009f: Merge panel: an overview of what the merge will do instead of just counts. Picked rows are grouped into Update, Rename, Add and Remove, each with a count per type and, for changed rows, which fields differ; click a row to select it in the list. The confirm dialog uses the same summary.

### Patch Changes

- e16009f: Preview: children parented with an offset (Ctrl+P) no longer jump when their parent is shown in another version. Re-pointing a preview copy at the chosen parent reset its parent inverse matrix; it is now kept.

## 0.2.0

### Minor Changes

- 44fbb1b: Changes list: a search field filters rows by name, toggles filter by kind of change (changed, renamed, new, missing, identical) with counts, and selecting a row selects what it affects in the preview and reveals it in the Outliner. Solo frames the picked row, keeps it selected when switching versions (re-framing only if it leaves the view) and no longer shows the incoming scene camera.

## 0.1.0

### Minor Changes

- bc8ef2e: New Blender extension: Compare & Merge. A "Compare & Merge" workspace tab diffs the open file against another version of it (objects, meshes, materials, images, cameras, lights, actions, armatures, curves, worlds, collections, scene settings), previews the merge result and either version of any row (Current / Incoming views, Ghost, Blink, Solo) in a runtime-only scene that saving never writes, and merges the picked versions with one undoable step. `blend-diff` prints the same diff from the command line.
