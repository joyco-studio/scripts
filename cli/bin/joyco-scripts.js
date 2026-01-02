#!/usr/bin/env node
"use strict";

const path = require("path");
const { Command } = require("commander");
const fg = require("fast-glob");
const { spawnSync } = require("child_process");
const readline = require("readline");
const commandsDoc = require("../commands.json");
const packageJson = require("../package.json");

const pkgRoot = path.resolve(__dirname, "..");
const scriptsDir = path.join(pkgRoot, "tools");

function runCommand(bin, args, options = {}) {
  const result = spawnSync(bin, args, { stdio: "inherit", ...options });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function parseNumber(value) {
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    throw new Error(`Expected a number but received: ${value}`);
  }
  return parsed;
}

function checkBinary(bin, args = ["--version"]) {
  const result = spawnSync(bin, args, { stdio: "ignore" });
  return result.status === 0;
}

function checkPythonModule(moduleName) {
  const result = spawnSync("python3", ["-c", `import ${moduleName}`], {
    stdio: "ignore",
  });
  return result.status === 0;
}

function promptYesNo(question) {
  if (!process.stdin.isTTY) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question(question, (answer) => {
      rl.close();
      const normalized = answer.trim().toLowerCase();
      resolve(["y", "yes"].includes(normalized));
    });
  });
}

async function ensurePythonWithPillow() {
  if (!checkBinary("python3")) {
    console.error("python3 is required but was not found in PATH.");
    process.exit(1);
  }

  if (checkPythonModule("PIL")) {
    return;
  }

  console.error("Python dependency missing: Pillow (PIL).");
  const approved = await promptYesNo("Install Pillow now using pip? (y/N) ");
  if (!approved) {
    console.error("Install aborted. Run: python3 -m pip install pillow");
    process.exit(1);
  }

  runCommand("python3", ["-m", "pip", "install", "pillow"]);
}

function ensureNode() {
  if (!checkBinary(process.execPath, ["--version"])) {
    console.error("Node.js is required but was not found.");
    process.exit(1);
  }
}

function resolveScript(name) {
  return path.join(scriptsDir, name);
}

function runPythonScript(scriptName, args) {
  runCommand("python3", [resolveScript(scriptName), ...args]);
}

async function handleCompress(srcPath, destPath, options) {
  await ensurePythonWithPillow();
  const args = [srcPath, destPath, "--quality", String(options.quality)];
  runPythonScript("compress.py", args);
}

async function handleRename(srcPath, destPattern, options) {
  if (!checkBinary("python3")) {
    console.error("python3 is required but was not found in PATH.");
    process.exit(1);
  }
  const args = [srcPath, destPattern, "-z", String(options.zeroPadding)];
  runPythonScript("rename.py", args);
}

async function handleResize(srcPath, destPath, options) {
  await ensurePythonWithPillow();

  const hasScale = typeof options.scale === "number";
  const hasWidth = typeof options.width === "number";
  const hasHeight = typeof options.height === "number";

  if (hasScale && (hasWidth || hasHeight)) {
    console.error("Use either --scale or --width/--height, not both.");
    process.exit(1);
  }

  if (!hasScale && !(hasWidth && hasHeight)) {
    console.error("Provide --scale or both --width and --height.");
    process.exit(1);
  }

  const args = [srcPath, destPath];
  if (hasScale) {
    args.push("--scale", String(options.scale));
  }
  if (hasWidth) {
    args.push("--width", String(options.width));
  }
  if (hasHeight) {
    args.push("--height", String(options.height));
  }

  runPythonScript("resize.py", args);
}

async function handleFixSvg(paths, options) {
  ensureNode();

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

function buildProgram() {
  const program = new Command();
  program.name(commandsDoc.cliName);
  program.description(commandsDoc.description);
  program.version(packageJson.version);

  const handlers = {
    compress: handleCompress,
    rename: handleRename,
    resize: handleResize,
    "fix-svg": handleFixSvg,
  };

  for (const commandDoc of commandsDoc.commands) {
    const cmd = program
      .command(commandDoc.name)
      .description(commandDoc.summary);

    if (commandDoc.usage) {
      cmd.usage(commandDoc.usage.replace(`${commandsDoc.cliName} `, ""));
    }

    if (Array.isArray(commandDoc.args)) {
      for (const arg of commandDoc.args) {
        const argSyntax = arg.required ? `<${arg.name}>` : `[${arg.name}]`;
        cmd.argument(argSyntax, arg.description || "");
      }
    }

    if (Array.isArray(commandDoc.options)) {
      for (const option of commandDoc.options) {
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

    if (Array.isArray(commandDoc.examples) && commandDoc.examples.length > 0) {
      const formatted = commandDoc.examples.map((example) => `  ${example}`).join("\n");
      cmd.addHelpText("after", `\nExamples:\n${formatted}\n`);
    }

    if (handlers[commandDoc.name]) {
      cmd.action(async (...actionArgs) => {
        const options = actionArgs.pop();
        const args = actionArgs;
        await handlers[commandDoc.name](...args, options);
      });
    }
  }

  return program;
}

buildProgram().parse(process.argv);
