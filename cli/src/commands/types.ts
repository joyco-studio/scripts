export type CommandArg = {
  name: string;
  required?: boolean;
  description?: string;
};

export type CommandOption = {
  flags: string;
  type?: "number" | "string" | "boolean";
  default?: unknown;
  description?: string;
};

export type CommandDefinition = {
  name: string;
  summary: string;
  usage?: string;
  args?: CommandArg[];
  options?: CommandOption[];
  examples?: string[];
};

export type CommandModule = {
  definition: CommandDefinition;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  handler: (...args: any[]) => Promise<void> | void;
};
