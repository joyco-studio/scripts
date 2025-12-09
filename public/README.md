# Public Scripts

Scripts designed to be fetched and executed directly via HTTP. Perfect for adding to your `package.json` scripts without cluttering your project.

## Setup

Add these to your `package.json` scripts:

```json
{
  "scripts": {
    "compress": "curl -s https://raw.githubusercontent.com/joyco-studio/scripts/main/public/compress.py | python3 -",
    "rename": "curl -s https://raw.githubusercontent.com/joyco-studio/scripts/main/public/rename.py | python3 -",
    "resize": "curl -s https://raw.githubusercontent.com/joyco-studio/scripts/main/public/resize.py | python3 -"
  }
}
```

---

## Scripts

### `compress.py`

Batch compress images to WebP format with quality control. Outputs a summary with size reduction stats.

```bash
python3 compress.py <src_path> <dest_path> --quality 80
```

| Argument | Description |
|----------|-------------|
| `src_path` | Input directory containing images |
| `dest_path` | Output directory for compressed images |
| `--quality` | WebP quality (0-100), default: 80 |

**Supported formats:** jpg, jpeg, png, bmp, tiff, webp

---

### `rename.py`

Copy and rename files with sequential numbering. Ideal for organizing image sequences.

```bash
python3 rename.py [-z zero_padding] <src_path> <dest_path/pattern_%n.png>
```

| Argument | Description |
|----------|-------------|
| `src_path` | Source directory with files to rename |
| `pattern_%n` | Output pattern where `%n` becomes the sequence number |
| `-z` | Zero padding width (default: 2) |

**Example:**

```bash
python3 rename.py -z 4 ./frames ./output/frame_%n.png
# Creates: frame_0000.png, frame_0001.png, frame_0002.png, ...
```

---

### `resize.py`

Batch resize images by exact dimensions or scale factor.

```bash
python3 resize.py <src_path> <dest_path> [--width W --height H | --scale S]
```

| Argument | Description |
|----------|-------------|
| `src_path` | Input directory containing images |
| `dest_path` | Output directory for resized images |
| `--width` | Target width in pixels |
| `--height` | Target height in pixels |
| `--scale` | Scale factor (e.g., 0.5 for half size) |

**Examples:**

```bash
# Scale down by 50%
python3 resize.py ./images ./output --scale 0.5

# Resize to exact dimensions
python3 resize.py ./images ./output --width 1920 --height 1080
```

**Supported formats:** jpg, jpeg, png, bmp, tiff, webp


