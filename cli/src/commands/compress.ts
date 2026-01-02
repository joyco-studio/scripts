import type { CommandModule } from "./types";

const definition: CommandModule["definition"] = {
  name: "compress",
  summary: "Batch compress images to WebP format with quality control.",
  usage: "scripts compress <src_path> <dest_path> [--quality <number>]",
  args: [
    {
      name: "src_path",
      required: true,
      description: "Input directory containing images.",
    },
    {
      name: "dest_path",
      required: true,
      description: "Output directory for compressed images.",
    },
  ],
  options: [
    {
      flags: "--quality <number>",
      type: "number",
      default: 80,
      description: "WebP quality (0-100).",
    },
  ],
  examples: ["scripts compress ./images ./output --quality 80"],
};

async function handler(
  srcPath: string,
  destPath: string,
  options: { quality: number }
) {
  const { compressImagesToWebp } = await import("../core/compress");
  const summary = await compressImagesToWebp({
    inputDir: srcPath,
    outputDir: destPath,
    quality: options.quality,
  });

  for (const item of summary.results) {
    console.log(`Compressed ${item.filename} and saved as ${item.outputPath}`);
  }

  console.log("\nSummary:");
  console.log(`Total images processed: ${summary.results.length}`);
  console.log(
    `Total size before compression: ${(summary.totalSizeBefore / 1024).toFixed(2)} KB`
  );
  console.log(
    `Total size after compression: ${(summary.totalSizeAfter / 1024).toFixed(2)} KB`
  );

  if (summary.totalSizeBefore > 0) {
    const reduction = summary.totalSizeBefore - summary.totalSizeAfter;
    const percent = (reduction / summary.totalSizeBefore) * 100;
    console.log(
      `Total size reduction: ${(reduction / 1024).toFixed(2)} KB (${percent.toFixed(2)}%)`
    );
  } else {
    console.log("Total size reduction: 0.00 KB (0.00%)");
  }
}

const command: CommandModule = {
  definition,
  handler,
};

export default command;
