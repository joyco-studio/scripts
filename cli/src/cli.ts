import { Command } from "commander";
import packageJson from "../package.json";
import registerCompress from "./commands/compress";
import registerSequence from "./commands/sequence";
import registerResize from "./commands/resize";
import registerFixSvg from "./commands/fix-svg";
import registerAgents from "./commands/agents";

const cliName = "scripts";
const cliDescription = "Joyco utility scripts bundled as a pnpx CLI.";
const commandRegistrations = [
  registerCompress,
  registerSequence,
  registerResize,
  registerFixSvg,
  registerAgents,
];

export function buildProgram() {
  const program = new Command();
  program.name(cliName);
  program.description(cliDescription);
  program.version(packageJson.version);

  for (const register of commandRegistrations) {
    register(program);
  }

  return program;
}

export function run(argv = process.argv) {
  buildProgram().parse(argv);
}

if (require.main === module) {
  run();
}
