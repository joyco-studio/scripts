import type { Command } from "commander";
import { renameFiles } from "../core/rename";
import { addExamples, handleCommandError, parseNumber } from "./utils";

export default function register(program: Command) {
  const command = program
    .command("sequence")
    .description("Copy and rename files with sequential numbering.")
    .usage("<src_path> <dest_pattern> [-z <number>]")
    .argument("<src_path>", "Source directory with files to rename.")
    .argument("<dest_pattern>", "Output pattern where %n becomes the sequence number.")
    .option("-z, --zero-padding <number>", "Zero padding width.", parseNumber, 2)
    .action(async (srcPath: string, destPattern: string, options: { zeroPadding: number }) => {
      try {
        const result = await renameFiles({
          srcDir: srcPath,
          targetPatternWithPath: destPattern,
          zeroPadding: options.zeroPadding,
          onPrepare: ({ sourcePath, tempPath }) => {
            console.log(`Prepared to copy: ${sourcePath} -> ${tempPath}`);
          },
        });

        if (result.empty) {
          console.log("No files found in the source directory.");
          return;
        }

        console.log("All files successfully copied and renamed.");
      } catch (error) {
        if (error instanceof Error) {
          if (
            error.message.startsWith("Source directory does not exist") ||
            error.message.startsWith("Target directory does not exist")
          ) {
            console.error(error.message);
            process.exit(1);
          }
          console.error(`An error occurred: ${error.message}`);
          process.exit(1);
        }
        handleCommandError(error);
      }
    });

  addExamples(command, ["scripts sequence -z 4 ./frames ./output/frame_%n.png"]);
}
