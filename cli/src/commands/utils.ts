import type { Command } from "commander";

export function parseNumber(value: string) {
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    throw new Error(`Expected a number but received: ${value}`);
  }
  return parsed;
}

export function addExamples(command: Command, examples: string[]) {
  if (!Array.isArray(examples) || examples.length === 0) {
    return;
  }
  const formatted = examples.map((example) => `  ${example}`).join("\n");
  command.addHelpText("after", `\nExamples:\n${formatted}\n`);
}

export function handleCommandError(error: unknown): never {
  if (error instanceof Error) {
    console.error(error.message);
  } else {
    console.error(String(error));
  }
  process.exit(1);
}
