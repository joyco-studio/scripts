import path from "path";
import fs from "fs/promises";
import { test } from "node:test";
import assert from "node:assert/strict";
import { compressImagesToWebp } from "../src/core/compress";
import { resizeImages } from "../src/core/resize";
import { renameFiles } from "../src/core/rename";
import {
  agentStrategies,
  pullAgents,
  type AgentStrategy,
} from "../src/core/agents";
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

test("renameFiles ignores hidden files like .DS_Store", async (t) => {
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await fs.mkdir(outputDir, { recursive: true });

  // Create hidden files that should be ignored
  await writeTextFile(path.join(inputDir, ".DS_Store"), "garbage");
  await writeTextFile(path.join(inputDir, ".hidden"), "secret");
  // Create actual files to be processed
  await writeTextFile(path.join(inputDir, "a.txt"), "a");
  await writeTextFile(path.join(inputDir, "b.txt"), "b");

  const result = await renameFiles({
    srcDir: inputDir,
    targetPatternWithPath: path.join(outputDir, "frame_%n"),
    zeroPadding: 2,
  });

  // Should only process the 2 non-hidden files
  assert.equal(result.processed, 2);

  const outputs = await fs.readdir(outputDir);
  // Hidden files should not appear in output and should not affect numbering
  assert.deepEqual(outputs.sort(), ["frame_00.txt", "frame_01.txt"].sort());
});

test("pullAgents writes AGENTS.md for each strategy", async (t) => {
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const contents = "# agents";
  globalThis.fetch = (async () => {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      text: async () => contents,
    };
  }) as typeof fetch;

  const strategies = Object.keys(agentStrategies) as AgentStrategy[];
  for (const strategy of strategies) {
    const result = await pullAgents({ strategy, cwd: tempDir });
    const expectedPath = path.resolve(tempDir, agentStrategies[strategy].defaultPath);
    assert.equal(result.outputPath, expectedPath);
    assert.equal(result.bytes, Buffer.byteLength(contents, "utf8"));
    const stored = await fs.readFile(expectedPath, "utf8");
    assert.equal(stored, contents);
  }
});

test("pullAgents respects write modes", async (t) => {
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  let currentContents = "first";
  globalThis.fetch = (async () => {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      text: async () => currentContents,
    };
  }) as typeof fetch;

  const strategy: AgentStrategy = "codex";
  const filePath = path.resolve(tempDir, agentStrategies[strategy].defaultPath);

  await pullAgents({ strategy, cwd: tempDir, writeMode: "overwrite" });
  let stored = await fs.readFile(filePath, "utf8");
  assert.equal(stored, "first");

  currentContents = "second";
  await pullAgents({ strategy, cwd: tempDir, writeMode: "append" });
  stored = await fs.readFile(filePath, "utf8");
  assert.equal(stored, "first\nsecond");

  currentContents = "third";
  await pullAgents({ strategy, cwd: tempDir, writeMode: "overwrite" });
  stored = await fs.readFile(filePath, "utf8");
  assert.equal(stored, "third");

  currentContents = "fourth";
  await assert.rejects(
    () => pullAgents({ strategy, cwd: tempDir, writeMode: "create" }),
    /EEXIST|exists|already/
  );
});
