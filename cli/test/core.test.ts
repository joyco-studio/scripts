import path from "path";
import fs from "fs/promises";
import { test } from "node:test";
import assert from "node:assert/strict";
import { compressImagesToWebp } from "../src/core/compress";
import { resizeImages } from "../src/core/resize";
import { renameFiles } from "../src/core/rename";
import {
  cleanupDir,
  createTempDir,
  readImageMetadata,
  writePngImage,
  writeTextFile,
} from "./helpers";

test("compressImagesToWebp writes webp outputs", async (t) => {
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await writePngImage(path.join(inputDir, "one.png"), 80, 60);
  await writePngImage(path.join(inputDir, "two.PNG"), 40, 40);

  const summary = await compressImagesToWebp({
    inputDir,
    outputDir,
    quality: 80,
  });

  assert.equal(summary.results.length, 2);
  assert.ok(summary.totalSizeBefore > 0);
  assert.ok(summary.totalSizeAfter > 0);

  const outputFiles = await fs.readdir(outputDir);
  assert.deepEqual(
    outputFiles.sort(),
    ["one.webp", "two.webp"].sort()
  );
});

test("resizeImages rescales images", async (t) => {
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await writePngImage(path.join(inputDir, "sample.png"), 120, 80);

  const result = await resizeImages({
    inputDir,
    outputDir,
    scale: 0.5,
  });

  assert.equal(result.results.length, 1);

  const outputPath = path.join(outputDir, "sample.png");
  const metadata = await readImageMetadata(outputPath);
  assert.equal(metadata.width, 60);
  assert.equal(metadata.height, 40);
});

test("renameFiles copies files with zero padding", async (t) => {
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await fs.mkdir(outputDir, { recursive: true });

  await writeTextFile(path.join(inputDir, "a.txt"), "a");
  await writeTextFile(path.join(inputDir, "b.txt"), "b");

  const result = await renameFiles({
    srcDir: inputDir,
    targetPatternWithPath: path.join(outputDir, "frame_%n"),
    zeroPadding: 3,
  });

  assert.equal(result.processed, 2);

  const outputs = await fs.readdir(outputDir);
  assert.deepEqual(outputs.sort(), ["frame_000.txt", "frame_001.txt"].sort());
});
