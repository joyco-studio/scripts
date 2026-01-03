import { build } from "esbuild";

await build({
  entryPoints: ["src/cli.ts"],
  bundle: true,
  platform: "node",
  target: "node18",
  format: "cjs",
  banner: { js: "#!/usr/bin/env node" },
  outfile: "dist/cli.js",
  external: ["sharp", "jscodeshift"],
});
