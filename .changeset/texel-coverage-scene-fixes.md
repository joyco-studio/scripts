---
"texel-coverage": patch
---

Analysis, cull and bake fixes found on a full test scene: glass and other transparent objects no longer hide what's behind them, faces that reach behind the camera are clipped and measured instead of counted as unseen, and objects hidden from rendering can still be baked.
