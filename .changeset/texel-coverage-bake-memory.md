---
"texel-coverage": patch
---

Bake to Suggested UVs no longer runs out of memory on big scenes: textures are never baked larger than their source, Cycles only sees the objects being baked, and each image is saved and freed right after its bake.
