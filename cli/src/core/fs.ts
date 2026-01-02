import fs from "fs/promises";

export async function listFiles(dir: string) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .sort();
}

export async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true });
}

export async function moveFile(source: string, destination: string) {
  try {
    await fs.rename(source, destination);
  } catch (error) {
    if (error && typeof error === "object" && "code" in error) {
      const errorCode = (error as { code?: string }).code;
      if (errorCode === "EXDEV") {
        await fs.copyFile(source, destination);
        await fs.unlink(source);
        return;
      }
    }
    throw error;
  }
}
