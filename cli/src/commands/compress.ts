import type { Command } from "commander";
import { addExamples, handleCommandError, parseNumber } from "./utils";
import { compressImagesToWebp } from "../core/compress";

export default function register(program: Command) {
  const command = program
    .command("compress")
    .description("Batch compress images to WebP format with quality control.")
    .usage("<src_path> <dest_path> [--quality <number>]")
    .argument("<src_path>", "Input directory containing images.")
    .argument("<dest_path>", "Output directory for compressed images.")
    .option("--quality <number>", "WebP quality (0-100).", parseNumber, 80)
    .action(async (srcPath: string, destPath: string, options: { quality: number }) => {
      try {
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
      } catch (error) {
        handleCommandError(error);
      }
    });

  addExamples(command, ["scripts compress ./images ./output --quality 80"]);
}
