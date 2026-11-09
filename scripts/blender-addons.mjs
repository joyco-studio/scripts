#!/usr/bin/env node
// Versioning and packaging for the Blender add-ons in blender/*.
//
// Each add-on is a private workspace package: its package.json "version" is the source of truth
// (bumped by Changesets) and "blender" says how to package it:
//   { "type": "extension", "source": "<folder with blender_manifest.toml>" }
//   { "type": "legacy",    "source": "<file.py with bl_info>" }
//
//   node scripts/blender-addons.mjs sync            write package versions into manifests / bl_info
//   node scripts/blender-addons.mjs check           exit 1 if any manifest disagrees with its package
//   node scripts/blender-addons.mjs build [name..]  build installable files into dist/blender/
//   node scripts/blender-addons.mjs release         build what Changesets just tagged and attach it to
//                                                   the GitHub release (PUBLISHED = publishedPackages JSON)

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const OUT = path.join(ROOT, "dist", "blender");

function addons() {
  const dir = path.join(ROOT, "blender");
  return fs
    .readdirSync(dir)
    .map((d) => path.join(dir, d, "package.json"))
    .filter((p) => fs.existsSync(p))
    .map((p) => {
      const pkg = JSON.parse(fs.readFileSync(p, "utf8"));
      if (!pkg.blender) return null;
      const src = path.join(path.dirname(p), pkg.blender.source);
      const versionFile = pkg.blender.type === "extension" ? path.join(src, "blender_manifest.toml") : src;
      return { name: pkg.name, version: pkg.version, type: pkg.blender.type, src, versionFile };
    })
    .filter(Boolean);
}

const PATTERNS = {
  extension: [/^version = "([^"]*)"/m, (v) => `version = "${v}"`],
  legacy: [/"version":\s*\(([^)]*)\)/, (v) => `"version": (${v.split(/[.-]/).slice(0, 3).join(", ")})`],
};

function current(a) {
  const m = fs.readFileSync(a.versionFile, "utf8").match(PATTERNS[a.type][0]);
  if (!m) throw new Error(`${a.name}: no version found in ${path.relative(ROOT, a.versionFile)}`);
  return a.type === "legacy" ? m[1].split(",").map((s) => s.trim()).join(".") : m[1];
}

function sync() {
  for (const a of addons()) {
    const [re, fmt] = PATTERNS[a.type];
    const text = fs.readFileSync(a.versionFile, "utf8");
    if (!re.test(text)) throw new Error(`${a.name}: no version found in ${path.relative(ROOT, a.versionFile)}`);
    fs.writeFileSync(a.versionFile, text.replace(re, fmt(a.version)));
    console.log(`${a.name} ${a.version} -> ${path.relative(ROOT, a.versionFile)}`);
  }
}

function check() {
  let bad = 0;
  for (const a of addons()) {
    const v = current(a);
    const ok = v === a.version.split("-")[0];
    console.log(`${ok ? "ok " : "BAD"} ${a.name}: package.json ${a.version}, ${path.relative(ROOT, a.versionFile)} ${v}`);
    if (!ok) bad++;
  }
  if (bad) {
    console.error("Run `node scripts/blender-addons.mjs sync` (pnpm version:package does it for you).");
    process.exit(1);
  }
}

function build(names = []) {
  fs.mkdirSync(OUT, { recursive: true });
  const out = [];
  for (const a of addons()) {
    if (names.length && !names.includes(a.name)) continue;
    if (current(a) !== a.version.split("-")[0]) throw new Error(`${a.name}: version out of sync, run sync first`);
    if (a.type === "extension") {
      // Same layout `blender --command extension build` produces: the folder's contents at the zip root.
      const id = fs.readFileSync(a.versionFile, "utf8").match(/^id = "([^"]*)"/m)[1];
      const file = path.join(OUT, `${id}-${a.version}.zip`);
      fs.rmSync(file, { force: true });
      execFileSync("zip", ["-qrX", file, ".", "-x", "*__pycache__*", "*.pyc", ".*", "*/.*"], { cwd: a.src });
      out.push({ ...a, file });
    } else {
      const file = path.join(OUT, `${path.basename(a.src, ".py")}-${a.version}.py`);
      fs.copyFileSync(a.src, file);
      out.push({ ...a, file });
    }
    console.log(`built ${path.relative(ROOT, out.at(-1).file)}`);
  }
  return out;
}

function release() {
  const published = JSON.parse(process.env.PUBLISHED || "[]");
  const ours = new Set(addons().map((a) => a.name));
  const names = published.filter((p) => ours.has(p.name)).map((p) => p.name);
  if (!names.length) return console.log("no Blender add-ons in this release");
  for (const a of build(names)) {
    const tag = `${a.name}@${a.version}`;
    const gh = (...args) => execFileSync("gh", args, { cwd: ROOT, stdio: "inherit" });
    try {
      execFileSync("gh", ["release", "view", tag], { cwd: ROOT, stdio: "ignore" });
    } catch {
      gh("release", "create", tag, "--title", tag, "--notes", `Blender add-on ${tag}`);
    }
    gh("release", "upload", tag, a.file, "--clobber");
  }
}

const [cmd, ...args] = process.argv.slice(2);
const commands = { sync, check, build: () => build(args), release };
if (!commands[cmd]) {
  console.error("usage: blender-addons.mjs sync | check | build [name..] | release");
  process.exit(1);
}
commands[cmd]();
