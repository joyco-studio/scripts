import path from "path";
import { ensureDir, listFiles } from "./fs";
import { isSupportedImage } from "./image-utils";

export type ResizeResult = {
  filename: string;
  inputPath: string;
  outputPath: string;
  width: number;
  height: number;
};

async function resolveScaledSize(inputPath: string, scale: number) {
  const sharp = (await import("sharp")).default;
  const metadata = await sharp(inputPath).metadata();
  if (!metadata.width || !metadata.height) {
    throw new Error(`Could not read image dimensions: ${inputPath}`);
  }

  return {
    width: Math.round(metadata.width * scale),
    height: Math.round(metadata.height * scale),
  };
}

export async function resizeImages({
  inputDir,
  outputDir,
  width,
  height,
  scale,
}: {
  inputDir: string;
  outputDir: string;
  width?: number;
  height?: number;
  scale?: number;
}): Promise<{ results: ResizeResult[] }> {
  const sharp = (await import("sharp")).default;

  await ensureDir(outputDir);

  const files = await listFiles(inputDir);
  const results: ResizeResult[] = [];

  for (const filename of files) {
    if (!isSupportedImage(filename)) {
      continue;
    }

    const inputPath = path.join(inputDir, filename);
    let targetWidth = width;
    let targetHeight = height;

    if (typeof scale === "number") {
      const scaled = await resolveScaledSize(inputPath, scale);
      targetWidth = scaled.width;
      targetHeight = scaled.height;
    }

    if (!targetWidth || !targetHeight) {
      continue;
    }

    const outputPath = path.join(outputDir, filename);

    await sharp(inputPath)
      .resize(targetWidth, targetHeight, { kernel: sharp.kernel.lanczos3 })
      .toFile(outputPath);

    results.push({
      filename,
      inputPath,
      outputPath,
      width: targetWidth,
      height: targetHeight,
    });
  }

  return { results };
}
