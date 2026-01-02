# CLI test commands

These commands exercise every CLI command using the images in `/Users/matiasperez/Desktop/temp/hedra-img-exports`.

```bash
pnpm build
```

```bash
node bin/joyco-scripts.js --help
```

```bash
node bin/joyco-scripts.js compress /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-compress --quality 80
```

```bash
node bin/joyco-scripts.js resize /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-resize-scale --scale 0.5
```

```bash
node bin/joyco-scripts.js resize /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-resize-dims --width 1920 --height 1080
```

```bash
mkdir -p /Users/matiasperez/Desktop/temp/hedra-img-exports-output-rename
```

```bash
node bin/joyco-scripts.js rename -z 4 /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-rename/frame_%n
```

```bash
node bin/joyco-scripts.js fix-svg /Users/matiasperez/Desktop/temp/hedra-img-exports --dry --print
```
