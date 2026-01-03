# CLI pnpx test commands

These commands exercise every CLI command using the images in `/Users/matiasperez/Desktop/temp/hedra-img-exports`.

```bash
pnpx @joycostudio/scripts --help
```

```bash
pnpx @joycostudio/scripts compress /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-compress --quality 80
```

```bash
pnpx @joycostudio/scripts resize /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-resize-scale --scale 0.5
```

```bash
pnpx @joycostudio/scripts resize /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-resize-dims --width 1920 --height 1080
```

```bash
mkdir -p /Users/matiasperez/Desktop/temp/hedra-img-exports-output-sequence
```

```bash
pnpx @joycostudio/scripts sequence -z 4 /Users/matiasperez/Desktop/temp/hedra-img-exports /Users/matiasperez/Desktop/temp/hedra-img-exports-output-sequence/frame_%n
```

```bash
pnpx @joycostudio/scripts fix-svg /Users/matiasperez/Desktop/temp/hedra-img-exports --dry --print
```
