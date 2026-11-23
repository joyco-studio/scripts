---
"compare-merge": patch
---

Images in a file saved on Windows are read correctly on macOS and Linux. Their paths keep backslashes (`//..\textures\a.png`, `C:\Users\...`), which were taken as part of the file name, so every image of a Windows artist's file showed as changed and its texture files couldn't be read for the content check.
