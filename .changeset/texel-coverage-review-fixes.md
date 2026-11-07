---
"texel-coverage": patch
---

Review fixes: linked duplicates no longer overwrite each other's cull, analysis or UV backups; a cull with no cameras refuses instead of removing every face; faces crossing the frame with no corner inside count as seen; rectangular textures and Mapping-node scales are measured correctly; camera selection is part of the cache fingerprint; `export --collection` optimizes what it exports; failed CLI actions exit 1; zero-gap UV packing can't hang. New: Compress with Chopper option in the sidebar and glTF dialog.
