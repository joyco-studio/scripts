---
"compare-merge": minor
---

Field-level picks: take some fields of a changed row from the incoming file and keep the rest, e.g. their UV map with your vertex positions on the same mesh. The Details panel groups the changed fields into units (transform, parent, a material slot, a UV map, positions, a shape key, a node input, a light's energy…), each with its own Current / Incoming; the row shows as Mixed. The preview shows the mix and Merge copies the picked units onto your data-block, which keeps its name and every user. Meshes whose topology differs, node wiring changes and modifier stack changes can only be taken whole.
