---
"compare-merge": patch
---

The diff now reads only what the files hold, not what this Blender session did to the open one:

- Images no longer show as changed when only their pixel size differs as "1024×1024 → —". The size was only known for images Blender had loaded (ones the viewport drew). The content hash still catches real pixel changes, including a resize.
- Keyframed and driven values (object transforms, pose bones, node inputs, light and camera settings, shape key values, custom properties) show as `animated` instead of the value at the playhead, which made every animated object look changed after scrubbing the timeline. Changes to the animation show on the action.
- Meshes in Edit Mode are compared with their unsaved edits. Before, a moved vertex was missed and the mesh showed fake topology and attribute changes.
- UDIM images hash every tile and image sequences every frame. Before, UDIM images had no content hash at all and sequences hashed only their first frame, so a repainted tile or frame went unnoticed.
