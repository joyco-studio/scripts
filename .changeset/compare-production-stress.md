---
"compare-merge": patch
---

Fixes from stress-testing on a production scene:

- Geometry Nodes modifier inputs are compared (a Smooth by Angle's angle, any group input, or the attribute it reads instead) and can be picked with the modifier's other settings. Picking any modifier's settings no longer fails on Blender 5.3, which gives modifiers no ID properties.
- Settings of Geometry Nodes nodes are compared (a Boolean Math or Compare node's operation, data types, domains). Blender defines them at runtime, and those properties were skipped.
- A modifier's viewport and render toggles are compared and picked with its settings.
- Opening or closing a panel in a modifier's UI is no longer a change.
- Images are compared by file name and content, not folder. Two versions saved in different folders no longer show every image as changed, and Merge no longer replaces them all.
- A rename is one row. What refers to the renamed data-block (material slots, a mesh's materials, a collection's objects…) reads it under its current name, so renaming a material used by 30 objects no longer lists those objects and their meshes too.

Open issues (NLA, collections, vertex group renames…) are listed in KNOWN-ISSUES.md.
