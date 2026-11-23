---
"compare-merge": patch
---

Follow-up fixes to comparing what the files hold:

- Packed images are compared again. Since the last release every packed image failed to read ("could not read"), so two different packed textures looked identical.
- Only the keyframed components of a value read `animated`: with just location X keyed, a change to Y or Z still shows, as `(animated, 1, 0)`.
- Drivers show on every row, not only on objects: their expression, variables and targets are compared for lights, cameras, materials and worlds (also drivers inside the node tree), meshes (shape key drivers) and the rest. The action of a material's node tree or of a mesh's shape keys is listed too. These come with the whole row when merging.
