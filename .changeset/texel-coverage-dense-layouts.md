---
"texel-coverage": minor
---

Suggest UVs packs far tighter, for any island shape: faces the export cull deletes get no texels, faces long enough to set the texture size are split, big islands are cut into grid chunks that each get their own density, Blender's outline packer places them, and the texture is the smallest power of two that holds the screen's 95th percentile within the new Size Tolerance (default 0.85). The report gains `culled` and `use` (texels the camera asks for over the texture's texels). On the test scene, texture memory went from 243 MB to 64 MB.
