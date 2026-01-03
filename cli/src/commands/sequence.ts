import { renameFiles } from "../core/rename";
import type { CommandModule } from "./types";

const definition: CommandModule["definition"] = {
  name: "sequence",
  summary: "Copy and rename files with sequential numbering.",
  usage: "scripts sequence <src_path> <dest_pattern> [-z <number>]",
  args: [
    {
      name: "src_path",
      required: true,
      description: "Source directory with files to rename.",
    },
    {
      name: "dest_pattern",
      required: true,
      description: "Output pattern where %n becomes the sequence number.",
    },
  ],
  options: [
    {
      flags: "-z, --zero-padding <number>",
      type: "number",
      default: 2,
      description: "Zero padding width.",
    },
  ],
  examples: ["scripts sequence -z 4 ./frames ./output/frame_%n.png"],
};

async function handler(
  srcPath: string,
  destPattern: string,
  options: { zeroPadding: number }
) {
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
    throw error;
  }
}

const command: CommandModule = {
  definition,
  handler,
};

export default command;
