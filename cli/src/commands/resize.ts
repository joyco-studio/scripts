import type { CommandModule } from "./types";

const definition: CommandModule["definition"] = {
  name: "resize",
  summary: "Batch resize images by exact dimensions or scale factor.",
  usage:
    "scripts resize <src_path> <dest_path> [--width <px> --height <px> | --scale <factor>]",
  args: [
    {
      name: "src_path",
      required: true,
      description: "Input directory containing images.",
    },
    {
      name: "dest_path",
      required: true,
      description: "Output directory for resized images.",
    },
  ],
  options: [
    {
      flags: "--width <px>",
      type: "number",
      description: "Target width in pixels.",
    },
    {
      flags: "--height <px>",
      type: "number",
      description: "Target height in pixels.",
    },
    {
      flags: "--scale <factor>",
      type: "number",
      description: "Scale factor (e.g., 0.5 for half size).",
    },
  ],
  examples: [
    "scripts resize ./images ./output --scale 0.5",
    "scripts resize ./images ./output --width 1920 --height 1080",
  ],
};

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

async function handler(
  srcPath: string,
  destPath: string,
  options: { width?: number; height?: number; scale?: number }
) {
  const { resizeImages } = await import("../core/resize");
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
}

const command: CommandModule = {
  definition,
  handler,
};

export { validateResizeOptions };
export default command;
