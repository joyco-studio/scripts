import fs from "fs/promises";
import os from "os";
import path from "path";
import { listFiles, moveFile } from "./fs";

type RenamePrepareCallback = (payload: {
  sourcePath: string;
  tempPath: string;
}) => void;

function formatCounter(counter: number, width: number) {
  return String(counter).padStart(width, "0");
}

function resolveExtension(filename: string) {
  const parts = filename.split(".");
  return parts[parts.length - 1];
}

async function ensureDirectoryExists(dirPath: string, errorMessage: string) {
  const stats = await fs.stat(dirPath).catch(() => null);
  if (!stats || !stats.isDirectory()) {
    throw new Error(errorMessage);
  }
}

export async function renameFiles({
  srcDir,
  targetPatternWithPath,
  zeroPadding,
  onPrepare,
}: {
  srcDir: string;
  targetPatternWithPath: string;
  zeroPadding: number;
  onPrepare?: RenamePrepareCallback;
}): Promise<{ processed: number; empty?: boolean }> {
  await ensureDirectoryExists(
    srcDir,
    `Source directory does not exist: ${srcDir}`
  );

  const targetDirectory = path.dirname(targetPatternWithPath);
  await ensureDirectoryExists(
    targetDirectory,
    `Target directory does not exist: ${targetDirectory}`
  );

  const targetPattern = path.basename(targetPatternWithPath);
  const files = await listFiles(srcDir);

  if (files.length === 0) {
    return { processed: 0, empty: true };
  }

  const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "joyco-rename-"));
  const operations: Array<{
    sourcePath: string;
    tempPath: string;
    finalPath: string;
  }> = [];

  try {
    let counter = 0;
    for (const filename of files) {
      const filePath = path.join(srcDir, filename);
      const extension = resolveExtension(filename);
      const formattedCounter = formatCounter(counter, zeroPadding);
      let newFile = targetPattern.replace("%n", formattedCounter);

      if (!newFile.endsWith(`.${extension}`)) {
        newFile = `${newFile}.${extension}`;
      }

      const tempPath = path.join(tmpDir, newFile);
      await fs.copyFile(filePath, tempPath);

      if (onPrepare) {
        onPrepare({ sourcePath: filePath, tempPath });
      }

      operations.push({
        sourcePath: filePath,
        tempPath,
        finalPath: path.join(targetDirectory, newFile),
      });
      counter += 1;
    }

    for (const operation of operations) {
      await moveFile(operation.tempPath, operation.finalPath);
    }

    return { processed: operations.length };
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true });
  }
}
