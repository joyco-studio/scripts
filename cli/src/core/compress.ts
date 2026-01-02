import path from "path";
import fs from "fs/promises";
import { ensureDir, listFiles } from "./fs";
import { isSupportedImage } from "./image-utils";

export type CompressResult = {
  filename: string;
  inputPath: string;
  outputPath: string;
  sizeBefore: number;
  sizeAfter: number;
};

export type CompressSummary = {
  results: CompressResult[];
  totalSizeBefore: number;
  totalSizeAfter: number;
};

export async function compressImagesToWebp({
  inputDir,
  outputDir,
  quality,
}: {
  inputDir: string;
  outputDir: string;
  quality: number;
}): Promise<CompressSummary> {
  const sharp = (await import("sharp")).default;

  await ensureDir(outputDir);

  const files = await listFiles(inputDir);
  const results: CompressResult[] = [];
  let totalSizeBefore = 0;
  let totalSizeAfter = 0;

  for (const filename of files) {
    if (!isSupportedImage(filename)) {
      continue;
    }

    const inputPath = path.join(inputDir, filename);
    const outputName = `${path.parse(filename).name}.webp`;
    const outputPath = path.join(outputDir, outputName);

    const { size: sizeBefore } = await fs.stat(inputPath);
    totalSizeBefore += sizeBefore;

    await sharp(inputPath).webp({ quality }).toFile(outputPath);

    const { size: sizeAfter } = await fs.stat(outputPath);
    totalSizeAfter += sizeAfter;

    results.push({
      filename,
      inputPath,
      outputPath,
      sizeBefore,
      sizeAfter,
    });
  }

  return {
    results,
    totalSizeBefore,
    totalSizeAfter,
  };
}
