---
"texel-coverage": patch
---

Review fixes. The cache keys now bind each face's stored analysis and cull result to its geometry and include every object's transparency, so swapping identical meshes or making a wall see-through invalidates them. Shape keys, constraints and NLA count as motion. Camera moments with different clipping ranges are no longer merged. Triangles fully behind the camera no longer stop the analysis. Suggest UVs: locked islands are respected on the first run, an object with two UV maps on one image uses shelf packing, the fallback stores the size it really used, meshes with modifiers are never split, and culled faces only lose their texels when the optimized export will cull them. Include Cameras now decides both the panel Export button and the CLI.
