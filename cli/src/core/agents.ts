import fs from "fs/promises";
import path from "path";

export const AGENTS_URL = "https://registry.joyco.studio/AGENTS.md";

export const agentStrategies = {
  codex: {
    defaultPath: "AGENTS.md",
    description: "Codex reads AGENTS.md from the project root.",
  },
  claude: {
    defaultPath: "CLAUDE.md",
    description: "Claude Code reads CLAUDE.md from the project root.",
  },
  cursor: {
    defaultPath: "AGENTS.md",
    description: "Cursor reads AGENTS.md from the project root.",
  },
} as const;

export type AgentStrategy = keyof typeof agentStrategies;
export type AgentsWriteMode = "create" | "overwrite" | "append";

export function parseAgentStrategy(value: string): AgentStrategy {
  const normalized = value.trim().toLowerCase();
  if (normalized in agentStrategies) {
    return normalized as AgentStrategy;
  }
  const choices = Object.keys(agentStrategies).join(", ");
  throw new Error(`Unknown strategy "${value}". Choose one of: ${choices}.`);
}

export function getDefaultAgentsPath(strategy: AgentStrategy) {
  return agentStrategies[strategy].defaultPath;
}

export function resolveAgentsPath({
  strategy,
  outputPath,
  cwd = process.cwd(),
}: {
  strategy: AgentStrategy;
  outputPath?: string;
  cwd?: string;
}) {
  return path.resolve(cwd, outputPath ?? getDefaultAgentsPath(strategy));
}

async function fetchAgentsMd(url = AGENTS_URL) {
  const response = await fetch(url, {
    headers: {
      "Cache-Control": "no-cache",
    },
  });
  if (!response.ok) {
    throw new Error(
      `Failed to fetch ${url} (${response.status} ${response.statusText}).`
    );
  }
  return response.text();
}

async function writeAgentsFile(
  outputPath: string,
  contents: string,
  mode: AgentsWriteMode
) {
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  if (mode === "append") {
    const needsNewline = !contents.startsWith("\n") && contents.length > 0;
    await fs.appendFile(outputPath, needsNewline ? `\n${contents}` : contents, "utf8");
    return;
  }
  const flag = mode === "create" ? "wx" : "w";
  await fs.writeFile(outputPath, contents, { encoding: "utf8", flag });
}

export async function pullAgents({
  strategy,
  outputPath,
  writeMode = "create",
  cwd = process.cwd(),
}: {
  strategy: AgentStrategy;
  outputPath?: string;
  writeMode?: AgentsWriteMode;
  cwd?: string;
}) {
  const resolvedPath = resolveAgentsPath({ strategy, outputPath, cwd });
  const contents = await fetchAgentsMd();
  await writeAgentsFile(resolvedPath, contents, writeMode);
  return {
    outputPath: resolvedPath,
    bytes: Buffer.byteLength(contents, "utf8"),
  };
}
