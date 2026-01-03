# CLI test commands

These commands exercise every CLI command using the images in `/Users/matiasperez/Desktop/temp/hedra-img-exports`.

```bash
pnpm build
```

```bash
node dist/cli.js --help
```

```bash
node dist/cli.js compress ./temp ./temp-output-compress --quality 80
```

```bash
node dist/cli.js resize ./temp ./temp-output-resize-scale --scale 0.5
```

```bash
node dist/cli.js resize ./temp ./temp-output-resize-dims --width 1920 --height 1080
```

```bash
mkdir -p ./temp-output-sequence
```

```bash
node dist/cli.js sequence -z 4 ./temp ./temp-output-sequence/frame_%n
```

```bash
node dist/cli.js fix-svg ./temp --dry --print
```
---

# CLI pnpx test commands

These commands exercise every CLI command using the images in `./temp`.

```bash
pnpx @joycostudio/scripts --help
```

```bash
pnpx @joycostudio/scripts compress ./temp ./temp-output-compress --quality 80
```

```bash
pnpx @joycostudio/scripts resize ./temp ./temp-output-resize-scale --scale 0.5
```

```bash
pnpx @joycostudio/scripts resize ./temp ./temp-output-resize-dims --width 1920 --height 1080
```

```bash
mkdir -p ./temp-output-sequence
```

```bash
pnpx @joycostudio/scripts sequence -z 4 ./temp ./temp-output-sequence/frame_%n
```

```bash
pnpx @joycostudio/scripts fix-svg ./temp --dry --print
```
