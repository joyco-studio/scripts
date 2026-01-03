import fs from "fs/promises";
import path from "path";
import os from "os";
import { spawnSync } from "child_process";
import sharp from "sharp";

export const repoRoot = path.resolve(__dirname, "..");

export async function createTempDir(prefix = "joyco-test-") {
  return fs.mkdtemp(path.join(os.tmpdir(), prefix));
}

export async function ensureDir(dirPath: string) {
  await fs.mkdir(dirPath, { recursive: true });
}

export async function writeTextFile(filePath: string, contents: string) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, contents, "utf8");
}

export async function writePngImage(
  filePath: string,
  width = 64,
  height = 64
) {
  await ensureDir(path.dirname(filePath));
  const image = sharp({
    create: {
      width,
      height,
      channels: 3,
      background: { r: 120, g: 180, b: 200 },
    },
  });
  await image.png().toFile(filePath);
}

export async function readImageMetadata(filePath: string) {
  const image = sharp(filePath);
  return image.metadata();
}

let buildReady = false;
let lastBuiltAt = 0;

async function getLatestMtimeMs(dirPath: string) {
  let latest = 0;
  const entries = await fs.readdir(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      const nested = await getLatestMtimeMs(entryPath);
      latest = Math.max(latest, nested);
    } else if (entry.isFile()) {
      const stats = await fs.stat(entryPath);
      latest = Math.max(latest, stats.mtimeMs);
    }
  }
  return latest;
}

export async function ensureBuild() {
  const distPath = path.join(repoRoot, "dist", "cli.js");
  const configPath = path.join(repoRoot, "esbuild.config.mjs");
  const srcPath = path.join(repoRoot, "src");

  let distMtime = 0;
  try {
    const stats = await fs.stat(distPath);
    distMtime = stats.mtimeMs;
  } catch (error) {
    if (error && typeof error === "object" && "code" in error) {
      const errorCode = (error as { code?: string }).code;
      if (errorCode !== "ENOENT") {
        throw error;
      }
    }
  }

  if (buildReady && distMtime && distMtime >= lastBuiltAt) {
    return;
  }

  const [srcMtime, configStat] = await Promise.all([
    getLatestMtimeMs(srcPath),
    fs.stat(configPath),
  ]);
  const needsBuild = distMtime === 0 || distMtime < srcMtime || distMtime < configStat.mtimeMs;

  if (!needsBuild) {
    buildReady = true;
    lastBuiltAt = distMtime;
    return;
  }

  const result = spawnSync(process.execPath, ["esbuild.config.mjs"], {
    cwd: repoRoot,
    stdio: "inherit",
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error("Build failed while preparing CLI tests.");
  }

  const finalStats = await fs.stat(distPath);
  buildReady = true;
  lastBuiltAt = finalStats.mtimeMs;
}

export function runCli(args: string[]) {
  return spawnSync(process.execPath, ["bin/joyco-scripts.js", ...args], {
    cwd: repoRoot,
    encoding: "utf8",
  });
}

export async function cleanupDir(dirPath: string) {
  await fs.rm(dirPath, { recursive: true, force: true });
}
