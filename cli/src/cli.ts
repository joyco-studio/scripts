import { Command } from "commander";
import packageJson from "../package.json";
import compressCommand from "./commands/compress";
import renameCommand from "./commands/rename";
import resizeCommand from "./commands/resize";
import fixSvgCommand from "./commands/fix-svg";
import type { CommandModule } from "./commands/types";

const cliName = "scripts";
const cliDescription = "Joyco utility scripts bundled as a pnpx CLI.";
const commands: CommandModule[] = [
  compressCommand,
  renameCommand,
  resizeCommand,
  fixSvgCommand,
];

function parseNumber(value: string) {
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    throw new Error(`Expected a number but received: ${value}`);
  }
  return parsed;
}

function reportError(error: unknown) {
  if (error instanceof Error) {
    console.error(error.message);
  } else {
    console.error(String(error));
  }
  process.exit(1);
}

export function buildProgram() {
  const program = new Command();
  program.name(cliName);
  program.description(cliDescription);
  program.version(packageJson.version);

  for (const commandDoc of commands) {
    const cmd = program
      .command(commandDoc.definition.name)
      .description(commandDoc.definition.summary);

    if (commandDoc.definition.usage) {
      cmd.usage(commandDoc.definition.usage.replace(`${cliName} `, ""));
    }

    if (Array.isArray(commandDoc.definition.args)) {
      for (const arg of commandDoc.definition.args) {
        const argSyntax = arg.required ? `<${arg.name}>` : `[${arg.name}]`;
        cmd.argument(argSyntax, arg.description || "");
      }
    }

    if (Array.isArray(commandDoc.definition.options)) {
      for (const option of commandDoc.definition.options) {
        const needsNumber = option.type === "number";
        const hasDefault = typeof option.default !== "undefined";
        if (needsNumber && hasDefault) {
          cmd.option(option.flags, option.description, parseNumber, option.default);
        } else if (needsNumber) {
          cmd.option(option.flags, option.description, parseNumber);
        } else if (hasDefault) {
          cmd.option(option.flags, option.description, option.default);
        } else {
          cmd.option(option.flags, option.description);
        }
      }
    }

    if (
      Array.isArray(commandDoc.definition.examples) &&
      commandDoc.definition.examples.length > 0
    ) {
      const formatted = commandDoc.definition.examples
        .map((example) => `  ${example}`)
        .join("\n");
      cmd.addHelpText("after", `\nExamples:\n${formatted}\n`);
    }

    cmd.action(async (...actionArgs) => {
      const options = actionArgs.pop();
      const args = actionArgs;
      try {
        await commandDoc.handler(...args, options);
      } catch (error) {
        reportError(error);
      }
    });
  }

  return program;
}

export function run(argv = process.argv) {
  buildProgram().parse(argv);
}
