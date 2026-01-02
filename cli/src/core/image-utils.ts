import path from "path";

export const SUPPORTED_IMAGE_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".bmp",
  ".tiff",
  ".webp",
]);

export function isSupportedImage(filename: string) {
  const extension = path.extname(filename).toLowerCase();
  return SUPPORTED_IMAGE_EXTENSIONS.has(extension);
}
