# compare-merge

## 0.2.0

### Minor Changes

- 44fbb1b: Changes list: a search field filters rows by name, toggles filter by kind of change (changed, renamed, new, missing, identical) with counts, and selecting a row selects what it affects in the preview and reveals it in the Outliner. Solo frames the picked row, keeps it selected when switching versions (re-framing only if it leaves the view) and no longer shows the incoming scene camera.

## 0.1.0

### Minor Changes

- bc8ef2e: New Blender extension: Compare & Merge. A "Compare & Merge" workspace tab diffs the open file against another version of it (objects, meshes, materials, images, cameras, lights, actions, armatures, curves, worlds, collections, scene settings), previews the merge result and either version of any row (Current / Incoming views, Ghost, Blink, Solo) in a runtime-only scene that saving never writes, and merges the picked versions with one undoable step. `blend-diff` prints the same diff from the command line.
