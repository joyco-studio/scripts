import type { Command } from "commander";
import { addExamples, handleCommandError, parseNumber } from "./utils";
import { resizeImages } from "../core/resize";

function validateResizeOptions(options: {
  scale?: number;
  width?: number;
  height?: number;
}) {
  const hasScale = typeof options.scale === "number";
  const hasWidth = typeof options.width === "number";
  const hasHeight = typeof options.height === "number";

  if (hasScale && (hasWidth || hasHeight)) {
    throw new Error("Use either --scale or --width/--height, not both.");
  }

  if (!hasScale && !(hasWidth && hasHeight)) {
    throw new Error("Provide --scale or both --width and --height.");
  }
}

export default function register(program: Command) {
  const command = program
    .command("resize")
    .description("Batch resize images by exact dimensions or scale factor.")
    .usage("<src_path> <dest_path> [--width <px> --height <px> | --scale <factor>]")
    .argument("<src_path>", "Input directory containing images.")
    .argument("<dest_path>", "Output directory for resized images.")
    .option("--width <px>", "Target width in pixels.", parseNumber)
    .option("--height <px>", "Target height in pixels.", parseNumber)
    .option("--scale <factor>", "Scale factor (e.g., 0.5 for half size).", parseNumber)
    .action(async (srcPath: string, destPath: string, options: { width?: number; height?: number; scale?: number }) => {
      try {
        validateResizeOptions(options);

        const result = await resizeImages({
          inputDir: srcPath,
          outputDir: destPath,
          width: options.width,
          height: options.height,
          scale: options.scale,
        });

        for (const item of result.results) {
          console.log(`Resized ${item.filename} and saved to ${item.outputPath}`);
        }
      } catch (error) {
        handleCommandError(error);
      }
    });

  addExamples(command, [
    "scripts resize ./images ./output --scale 0.5",
    "scripts resize ./images ./output --width 1920 --height 1080",
  ]);
}

export { validateResizeOptions };
