import path from "path";
import fg from "fast-glob";
import { spawnSync } from "child_process";
import type { CommandModule } from "./types";

const pkgRoot = path.resolve(__dirname, "..", "..");
const scriptsDir = path.join(pkgRoot, "tools");

const definition: CommandModule["definition"] = {
  name: "fix-svg",
  summary: "Fix SVG kebab-case attributes to JSX camelCase in TSX files.",
  usage: "scripts fix-svg [paths...] [--dry] [--print]",
  args: [
    {
      name: "paths",
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
  const result = spawnSync(bin, args, { stdio: "inherit", ...options });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

async function handler(paths: string[], options: { dry?: boolean; print?: boolean }) {
  const targetPaths = paths.length > 0 ? paths : [process.cwd()];
  const files = await fg(targetPaths, {
    onlyFiles: true,
    unique: true,
    ignore: ["**/node_modules/**"],
    extglob: true,
    globstar: true,
    expandDirectories: {
      extensions: ["tsx"],
    },
  });

  if (files.length === 0) {
    console.error("No .tsx files found.");
    process.exit(1);
  }

  const transformPath = resolveScript("fix-svg-jsx-attrs.js");
  const jscodeshiftPath = require.resolve("jscodeshift/bin/jscodeshift.js");
  const args = [
    jscodeshiftPath,
    "--parser=tsx",
    "-t",
    transformPath,
    ...files,
  ];

  if (options.dry) {
    args.push("--dry");
  }

  if (options.print) {
    args.push("--print");
  }

  runCommand(process.execPath, args);
}

const command: CommandModule = {
  definition,
  handler,
};

export default command;
