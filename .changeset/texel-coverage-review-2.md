---
"texel-coverage": patch
---

Second review: bake skips textures it can't convert safely (alpha, transformed coordinates, mixed normal/colour use, several UV maps) and only switches a layout when every texture on it converts, with rollback on failure; linked copies are sized from the largest copy and can be selected individually; cull and verify use private temp folders, always restore hidden objects, and keep the cull modifier's stack position.
