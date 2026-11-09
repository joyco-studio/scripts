---
"texel-coverage": patch
---

No more duplicated work between stages. The geometry key is world-space for plain static meshes, so renaming, splitting or joining objects keeps the stored analysis and cull valid; `core.restamp()` lets tools carry them over visibility-neutral edits (a join moves vertices by float noise). Identical camera moments are evaluated once in analysis, cull and the pixel pass. Suggest UVs computes the cull once and the export reuses it (a full run went from ~41 to ~23 min, export from 15 min to 8 s).
