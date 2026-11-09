---
"texel-coverage": patch
---

Suggest UVs packs faster: the texture size is estimated from the islands' real area and the concave packer runs once (at most one correction) instead of once per candidate size, and sliver chunks are merged into their neighbours. Same sizes and layout quality. Also fixes a crash on image names longer than 63 characters (planned sizes are stored as JSON).
