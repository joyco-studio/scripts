import path from "path";
import fs from "fs";
import { spawnSync } from "child_process";
import type { CommandModule } from "./types";

const scriptsDir = path.resolve(__dirname, "..", "lib", "codemod");

const definition: CommandModule["definition"] = {
  name: "fix-svg",
  summary: "Fix SVG kebab-case attributes to JSX camelCase in TSX files.",
  usage: "scripts fix-svg [paths...] [--dry] [--print]",
  args: [
    {
      name: "paths...",
      required: false,
      description: "Files or directories to process (defaults to current directory).",
    },
  ],
  options: [
    {
      flags: "--dry",
      description: "Run a dry pass without writing changes.",
    },
    {
      flags: "--print",
      description: "Print transformed files to stdout (use with --dry).",
    },
  ],
  examples: ["scripts fix-svg src", "scripts fix-svg src --dry --print"],
};

function resolveScript(name: string) {
  return path.join(scriptsDir, name);
}

function runCommand(
  bin: string,
  args: string[],
  options: { stdio?: "inherit" | "pipe" } = {}
) {
  const result = spawnSync(bin, args, {
    stdio: "inherit",
    encoding: "utf8",
    ...options,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
  return result;
}

async function handler(
  paths: string[] | string | undefined,
  options: { dry?: boolean; print?: boolean }
) {
  const normalizedPaths = Array.isArray(paths)
    ? paths
    : typeof paths === "string"
      ? [paths]
      : [];
  const targetPaths = normalizedPaths.length > 0 ? normalizedPaths : [process.cwd()];
  const transformPath = resolveScript("fix-svg-jsx-attrs.js");
  if (!fs.existsSync(transformPath)) {
    throw new Error(`Transform file not found: ${transformPath}`);
  }
  const jscodeshiftPath = require.resolve("jscodeshift/bin/jscodeshift.js");
  const args = [
    jscodeshiftPath,
    "--parser=tsx",
    "--extensions=tsx",
    "--ignore-pattern=**/node_modules/**",
    "-t",
    transformPath,
    ...targetPaths,
  ];

  if (options.dry) {
    args.push("--dry");
  }

  if (options.print) {
    args.push("--print");
  }

  const stdio = options.print ? "pipe" : "inherit";
  const result = runCommand(process.execPath, args, { stdio });

  if (options.print) {
    if (result.stdout) {
      process.stdout.write(result.stdout);
    }
    if (result.stderr) {
      process.stderr.write(result.stderr);
    }
  }
}

const command: CommandModule = {
  definition,
  handler,
};

export default command;
