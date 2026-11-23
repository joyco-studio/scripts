# Known issues

Found by stress-testing 0.3.1 on a production scene (412 objects, 357 Geometry Nodes modifiers, 275 images, NLA animation, 276 MB) on Blender 5.3. Each change type was applied to a copy of the file. The copy was diffed against the original, everything was merged both ways, and the result was diffed again in the session and after saving and reopening. Everything not listed here was detected and merged cleanly.

These are still open. The fixed ones are in the changelog. Each issue has the cause and a direction for the fix.

## 1. NLA isn't compared

**What happens.** Moving or scaling an NLA strip, adding a track or swapping a strip's action shows no difference. Muting a track only shows up indirectly: the values it animated stop reading `animated`, so location / rotation / scale appear changed. A strip whose action changes shows its old action as only in this file, and that row stays after the merge.

**Why it matters.** glTF exports every NLA track as an animation. In the test file all 26 actions live in NLA strips, with no active action.

**Cause.** `snap_object` (`snapshot.py`) reads only `animation_data.action` and its slot; `anim_paths` reads NLA strips only to mask animated values.

**Fix direction.** Add an `nla` field per track to objects (and to anything else with `animation_data`): track name, mute / solo, and per strip its action (via `ref()`), frame start / end, scale, repeat, blend type, extrapolation and influence. Make it one `animation` unit with the action, so a Mixed pick takes the whole animation. Merge already appends the incoming object with its NLA, so a whole-row pick works once the diff shows the change. `apply.copy_animation` needs to copy tracks for unit picks.

## 2. Vertex group renames can't be merged

**What happens.** Renaming a vertex group flags the object (`vertex groups`), but merging it changes nothing, so the row stays different after the merge in both directions.

**Cause.** Since Blender 3.0 vertex group names live on the mesh; `Object.vertex_groups` only shows them. The field is on the object row, but the mesh row reads identical (`vertex weights` hashes group *indices*, not names). Merge appends the incoming object, sees its mesh as unchanged and remaps it back to the current mesh, which still has the old names.

**Fix direction.** Move the field to the mesh: put the group names into the `vertex weights` digest (or into their own `vertex groups` field on `snap_mesh`) and drop it from `snap_object`. Picking the mesh (or the object, which cascades to its mesh) then brings the names.

## 3. Collection changes are listed but can't be merged

**What happens.** Collection rows show changes (render visibility, nesting, a collection only in one file) but have no Current / Incoming choice, so none of them merge:
- `hide_render` / `hide_viewport` / instance offset: stays as it is.
- Moving a collection under another one: stays.
- A collection removed in the incoming file stays. A new one only appears when a new object in it is added.
- Renaming a collection shows as one rename row, but the collection keeps its current name.

**Cause.** `session.choosable()` returns False for every `COLLECTION` row, and `merge.plan` never brings collections. Objects get their incoming membership through `ensure_collection`, which creates a missing collection but never renames, moves or removes one.

**Fix direction.** Make collection rows choosable for their settings (`hide_render`, `hide_viewport`, `instance_offset`, parents) and renames. Apply them in `merge.run` on the local collection rather than by appending: appending a collection brings every object in it. Treat Remove like objects: unlink it and re-link its children to its parents. The `objects` field can stay informational, since each object's own `collections` unit already moves it.

## 4. Data only in the incoming file that no object uses can't be added

**What happens.** A material (or image, node group, action…) that only the incoming file has and that nothing uses (kept with a fake user) is listed as `+` but can't be picked. The same goes for the reverse: a `−` row that isn't an object can't be removed.

**Cause.** `session.choosable()` only allows `+` / `−` on objects. Other types come along with the objects that use them.

**Fix direction.** Allow Add on non-object `+` rows that have a fake user, or no users at all (`merge.plan` would bring them like anything else). For `−` rows, allow Remove only when nothing in the merged file would still use them.

## 5. Orphaned data stays in the list after a merge

**What happens.** Merging the removal of an object leaves its mesh listed as `−` until the file is saved and reopened. The same happens to an image no longer used after taking a material that points at another one. The result is right: Blender doesn't save data-blocks nobody uses. But the list says the merge left something behind.

**Fix direction.** At the end of `merge.run`, remove the current data-blocks that the merge left with zero users and that only the current file had (the `−` rows). Only those: unused data that was already in the file is the user's.

## 6. Not compared

- **The UV map used for rendering** (`active_render`): glTF exports it as `TEXCOORD_0`. Skipped by the `active_` prefix in `SKIP_PREFIX`. Fix: add a `render uv` field to `snap_mesh` (a `materials`-like unit).
- **Excluding a collection from the view layer**: changes what an export with "visible only" / "active collection" contains. It's view-layer state (`LayerCollection.exclude`), not on the collection. Fix: read the compared scene's view layer in `snap_collection` (the incoming scene is read once in `compare`, like the scene settings).
- **Hiding an object with the eye icon** (`hide_get`, view-layer state): probably right to ignore, but it changes "visible only" exports. Decide, then document.

## 7. Float precision

Values are written with 6 significant digits (`fnum`). An object 100 m from the origin hides moves under a millimetre, and one 1000 m away hides moves under a centimetre. Mesh positions, UVs and keyframes are hashed exactly and aren't affected. Fix: compare with a relative tolerance (or 9 significant digits) and keep 6 digits for display. The comparison string and the display string would then have to be separate.

## 8. Geometry Nodes inputs on older Blender: not verified

Blender 5.3 exposes Geometry Nodes modifier inputs as `mod.properties.inputs` and gives modifiers no ID properties at all. Older versions keep the inputs as ID properties (`mod["Input_1"]`). `snapshot.gn_input_fields` and `apply.copy_gn_inputs` read and write both, but only the 5.3 path is tested. The extension declares 5.1 as its minimum, so run `tests/run` on 5.1 before the next release.

## Notes for testing

- Blender itself drops an image whose user count is 1 but that nothing uses after two saves (`almohadon_mat_normal` in the test file). Resave a file twice before using it as a test baseline, or this shows up as a difference after every merge round trip.
- A Geometry Nodes modifier pinned to the end of the stack (`use_pin_to_last`, like Smooth by Angle) can't be moved before other modifiers, so a reorder test has to move a different one.
