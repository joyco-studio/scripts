import path from "path";
import fs from "fs/promises";
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  cleanupDir,
  createTempDir,
  ensureBuild,
  runCli,
  writePngImage,
  writeTextFile,
} from "./helpers";

test("cli --help prints usage", async (t) => {
  await ensureBuild();
  const result = runCli(["--help"]);
  assert.equal(result.status, 0);
  assert.match(result.stdout, /scripts/i);
});

test("cli compress writes webp outputs", async (t) => {
  await ensureBuild();
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await writePngImage(path.join(inputDir, "one.png"), 64, 64);

  const result = runCli([
    "compress",
    inputDir,
    outputDir,
    "--quality",
    "80",
  ]);

  assert.equal(result.status, 0);

  const outputs = await fs.readdir(outputDir);
  assert.deepEqual(outputs, ["one.webp"]);
});

test("cli resize rejects conflicting options", async (t) => {
  await ensureBuild();
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await writePngImage(path.join(inputDir, "one.png"), 64, 64);

  const result = runCli([
    "resize",
    inputDir,
    outputDir,
    "--scale",
    "0.5",
    "--width",
    "100",
  ]);

  assert.notEqual(result.status, 0);
  assert.match(result.stderr ?? "", /Use either --scale or --width\/--height/);
});

test("cli sequence copies files", async (t) => {
  await ensureBuild();
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputDir = path.join(tempDir, "input");
  const outputDir = path.join(tempDir, "output");
  await fs.mkdir(outputDir, { recursive: true });
  await writeTextFile(path.join(inputDir, "a.txt"), "a");
  await writeTextFile(path.join(inputDir, "b.txt"), "b");

  const result = runCli([
    "sequence",
    "-z",
    "2",
    inputDir,
    path.join(outputDir, "frame_%n"),
  ]);

  assert.equal(result.status, 0);

  const outputs = await fs.readdir(outputDir);
  assert.deepEqual(outputs.sort(), ["frame_00.txt", "frame_01.txt"].sort());
});

test("cli fix-svg prints transformed output", async (t) => {
  await ensureBuild();
  const tempDir = await createTempDir();
  t.after(() => cleanupDir(tempDir));

  const inputFile = path.join(tempDir, "icon.tsx");
  await writeTextFile(
    inputFile,
    "export const Icon = () => (<svg stroke-width=\"2\" />);"
  );

  const result = runCli(["fix-svg", inputFile, "--dry", "--print"]);
  assert.equal(result.status, 0);
  assert.match(result.stdout ?? "", /strokeWidth/);
});
