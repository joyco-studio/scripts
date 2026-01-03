import path from "path";
import fs from "fs/promises";
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import type { Command } from "commander";
import { addExamples, handleCommandError } from "./utils";
import {
  agentStrategies,
  resolveAgentsPath,
  parseAgentStrategy,
  pullAgents,
  type AgentStrategy,
  type AgentsWriteMode,
} from "../core/agents";

const strategyChoices = Object.entries(agentStrategies)
  .map(([key, value]) => `${key}=${value.defaultPath}`)
  .join(", ");

async function fileExists(filePath: string) {
  try {
    await fs.access(filePath);
    return true;
  } catch (error) {
    if (error && typeof error === "object" && "code" in error) {
      const errorCode = (error as { code?: string }).code;
      if (errorCode === "ENOENT") {
        return false;
      }
    }
    throw error;
  }
}

async function promptExistingFile(
  outputPath: string
): Promise<AgentsWriteMode | "cancel"> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error(
      "Output file already exists. Re-run in an interactive terminal to choose overwrite, append, or cancel."
    );
  }

  const rl = createInterface({ input, output });
  try {
    while (true) {
      const answer = await rl.question(
        `\nFile already exists at ${outputPath}. Overwrite (o), append (a), or cancel (c)? `
      );
      const normalized = answer.trim().toLowerCase();
      if (normalized === "o" || normalized === "overwrite") {
        return "overwrite";
      }
      if (normalized === "a" || normalized === "append") {
        return "append";
      }
      if (normalized === "c" || normalized === "cancel") {
        return "cancel";
      }
    }
  } finally {
    rl.close();
  }
}

export default function register(program: Command) {
  const command = program
    .command("agents")
    .description("Download the latest AGENTS.md for a selected agent tool strategy.")
    .usage("[dest_path] [-s <strategy>]")
    .argument(
      "[dest_path]",
      "Output path (defaults to the strategy's standard location)."
    )
    .option(
      "-s, --strategy <strategy>",
      `Agent tool strategy (${strategyChoices}).`,
      parseAgentStrategy,
      "codex"
    )
    .action(async (destPath: string | undefined, options: { strategy: AgentStrategy }, cmd) => {
      try {
        const strategySource = cmd.getOptionValueSource?.("strategy");
        if (destPath && strategySource === "cli") {
          throw new Error("Choose either an output path or -s/--strategy, not both.");
        }
        const resolvedPath = resolveAgentsPath({
          strategy: options.strategy,
          outputPath: destPath,
          cwd: process.cwd(),
        });
        const displayPath = path.relative(process.cwd(), resolvedPath);
        let writeMode: AgentsWriteMode = "create";
        if (await fileExists(resolvedPath)) {
          const choice = await promptExistingFile(displayPath);
          if (choice === "cancel") {
            console.log("Canceled.");
            process.exit(1);
          }
          writeMode = choice;
        }
        await pullAgents({
          strategy: options.strategy,
          outputPath: resolvedPath,
          writeMode,
        });
        console.log(`Saved agent instructions to ${displayPath}`);
      } catch (error) {
        handleCommandError(error);
      }
    });

  addExamples(command, [
    "scripts agents",
    "scripts agents -s claude",
    "scripts agents -s cursor",
    "scripts agents ./config/AGENTS.md",
  ]);
}
