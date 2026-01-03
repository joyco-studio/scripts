# CLI test commands

These commands exercise every CLI command using the images in `/Users/matiasperez/Desktop/temp/hedra-img-exports`.

```bash
pnpm build
```

```bash
node dist/cli.js --help
```

```bash
node dist/cli.js compress /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-compress --quality 80
```

```bash
node dist/cli.js resize /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-resize-scale --scale 0.5
```

```bash
node dist/cli.js resize /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-resize-dims --width 1920 --height 1080
```

```bash
mkdir -p /Users/matiasperez/Desktop/temp/hedra-img-exports-output-sequence
```

```bash
node dist/cli.js sequence -z 4 /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-sequence/frame_%n
```

```bash
node dist/cli.js fix-svg /Users/matiasperez/Desktop/temp/hedra-img-exports --dry --print
```
