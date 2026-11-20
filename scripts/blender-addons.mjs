#!/usr/bin/env node
// Versioning and packaging for the Blender add-ons in blender/*.
//
// Each add-on is a private workspace package: its package.json "version" is the source of truth
// (bumped by Changesets) and "blender" says how to package it:
//   { "type": "extension", "source": "<folder with blender_manifest.toml>" }
//   { "type": "legacy",    "source": "<file.py with bl_info>" }
//
//   node scripts/blender-addons.mjs sync               write package versions into manifests / bl_info
//   node scripts/blender-addons.mjs check              exit 1 if any manifest disagrees with its package
//   node scripts/blender-addons.mjs validate           check, plus `blender --command extension validate`
//   node scripts/blender-addons.mjs build [name..]     build installable files into dist/blender/
//   node scripts/blender-addons.mjs release <tag..>    build the add-on each `<name>@<version>` tag names,
//                                                      fail unless the tag, package.json and manifest agree,
//                                                      and attach the file to that GitHub release
//   node scripts/blender-addons.mjs pages <dir> <zip..>  add extension zips to the extensions repository in
//                                                      <dir> (the gh-pages checkout) and regenerate index.json
//
// Extensions are built with `blender --command extension build` when Blender is available ($BLENDER, or
// `blender` on PATH); without it `build` falls back to zip, which produces the same layout. `release`,
// `validate` and `pages` need Blender. ADDONS_ROOT points the script at another checkout (a release tag's
// worktree) while running this copy of it.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(process.env.ADDONS_ROOT || path.join(path.dirname(new URL(import.meta.url).pathname), ".."));
const OUT = path.join(ROOT, "dist", "blender");

function findBlender() {
  if (process.env.BLENDER) return process.env.BLENDER;
  try {
    execFileSync("blender", ["--version"], { stdio: "ignore" });
    return "blender";
  } catch {
    return null;
  }
}

function blender(...args) {
  const bin = findBlender();
  if (!bin) throw new Error("Blender not found: set BLENDER=/path/to/blender or put blender on PATH");
  execFileSync(bin, ["--factory-startup", "--command", "extension", ...args], { stdio: "inherit" });
}

const manifestField = (a, key) => fs.readFileSync(a.versionFile, "utf8").match(new RegExp(`^${key} = "([^"]*)"`, "m"))[1];

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

// Extension manifests carry the full version (0.3.0-test) and must match exactly; bl_info only has room
// for the numbers.
const inSync = (a, v = a.version) => current(a) === (a.type === "legacy" ? v.split("-")[0] : v);

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
    const ok = inSync(a);
    console.log(`${ok ? "ok " : "BAD"} ${a.name}: package.json ${a.version}, ${path.relative(ROOT, a.versionFile)} ${v}`);
    if (!ok) bad++;
  }
  if (bad) {
    console.error("Run `node scripts/blender-addons.mjs sync` (pnpm version:package does it for you).");
    process.exit(1);
  }
}

function validate() {
  check();
  for (const a of addons()) if (a.type === "extension") blender("validate", a.src);
}

function build(names = []) {
  fs.mkdirSync(OUT, { recursive: true });
  const out = [];
  const useBlender = !!findBlender();
  for (const a of addons()) {
    if (names.length && !names.includes(a.name)) continue;
    if (!inSync(a)) throw new Error(`${a.name}: version out of sync, run sync first`);
    if (a.type === "extension") {
      const file = path.join(OUT, `${manifestField(a, "id")}-${current(a)}.zip`);
      fs.rmSync(file, { force: true });
      if (useBlender) {
        // The official build validates the manifest too.
        blender("build", "--source-dir", a.src, "--output-dir", OUT);
      } else {
        // Same layout `blender --command extension build` produces: the folder's contents at the zip root.
        execFileSync("zip", ["-qrX", file, ".", "-x", "*__pycache__*", "*.pyc", ".*", "*/.*"], { cwd: a.src });
      }
      if (!fs.existsSync(file)) throw new Error(`${a.name}: expected ${path.relative(ROOT, file)} after the build`);
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

const INSTALL_START = "<!-- blender-install -->";
const INSTALL_END = "<!-- /blender-install -->";

function parseTag(tag) {
  const at = tag.lastIndexOf("@");
  if (at <= 0) throw new Error(`${tag}: expected a <name>@<version> tag`);
  return { name: tag.slice(0, at), version: tag.slice(at + 1) };
}

function release(tags) {
  if (!findBlender()) throw new Error("release needs Blender to validate and build extensions (set BLENDER)");
  const all = new Map(addons().map((a) => [a.name, a]));
  const names = [];
  for (const tag of tags) {
    const { name, version } = parseTag(tag);
    const a = all.get(name);
    if (!a) {
      console.log(`${tag}: not a Blender add-on, skipping`);
      continue;
    }
    // The tag, package.json and manifest / bl_info must all name the same version.
    const inFile = current(a);
    if (version !== a.version || !inSync(a, version)) {
      throw new Error(
        `${tag}: tag says ${version}, package.json says ${a.version}, ${path.relative(ROOT, a.versionFile)} says ${inFile}`,
      );
    }
    names.push(name);
  }
  if (!names.length) return console.log("no Blender add-ons in this release");
  for (const a of addons()) if (names.includes(a.name) && a.type === "extension") blender("validate", a.src);
  for (const a of build(names)) {
    const tag = `${a.name}@${a.version}`;
    const gh = (...args) => execFileSync("gh", args, { cwd: ROOT, stdio: "inherit" });
    const asset = path.basename(a.file);
    // GitHub always adds "Source code (zip)" to a release; say which file to install so nobody grabs that.
    // The section sits between markers so re-running replaces it and keeps the Changesets changelog.
    const install = [
      INSTALL_START,
      `**Install \`${asset}\`** from the assets below, not "Source code (zip)" (that's the whole repo).`,
      a.type === "extension"
        ? "In Blender: Edit > Preferences > Get Extensions > ⌄ > Install from Disk…, or drag the zip into Blender."
        : "In Blender: Edit > Preferences > Add-ons > ⌄ > Install from Disk…",
      INSTALL_END,
    ].join("\n");
    let body = null;
    try {
      body = execFileSync("gh", ["release", "view", tag, "--json", "body", "--jq", ".body"], {
        cwd: ROOT,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
    } catch {}
    if (body === null) {
      gh("release", "create", tag, "--title", tag, "--notes", `Blender add-on ${tag}.\n\n${install}`);
    } else {
      const start = body.indexOf(INSTALL_START);
      const end = body.indexOf(INSTALL_END);
      const rest = (start >= 0 && end > start ? body.slice(0, start) + body.slice(end + INSTALL_END.length) : body).trim();
      gh("release", "edit", tag, "--notes", rest ? `${install}\n\n${rest}` : install);
    }
    gh("release", "upload", tag, a.file, "--clobber");
  }
}

// Semantic-version order (build metadata ignored): 1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-beta < 1.0.0.
function compareSemver(x, y) {
  const parse = (v) => {
    const [core, pre] = v.split("+")[0].split(/-(.*)/s);
    return { core: core.split(".").map(Number), pre: pre ? pre.split(".") : [] };
  };
  const a = parse(x);
  const b = parse(y);
  for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] < b.core[i] ? -1 : 1;
  if (!a.pre.length || !b.pre.length) return b.pre.length - a.pre.length; // a release beats its prereleases
  for (let i = 0; i < Math.min(a.pre.length, b.pre.length); i++) {
    const [p, q] = [a.pre[i], b.pre[i]];
    if (p === q) continue;
    const [pn, qn] = [/^\d+$/.test(p), /^\d+$/.test(q)];
    if (pn && qn) return +p < +q ? -1 : 1;
    if (pn !== qn) return pn ? -1 : 1; // numeric identifiers sort before alphanumeric ones
    return p < q ? -1 : 1;
  }
  return a.pre.length - b.pre.length; // more identifiers wins when the shared ones are equal
}

// The extensions repository Blender reads (Preferences > Get Extensions > Repositories): the newest zip of
// each extension sits next to index.json, and older ones move to archive/ so they stay downloadable.
// server-generate only scans the top folder, and two versions of one id there would be listed as duplicates.
function pages(dir, zips) {
  if (!dir || !zips.length) throw new Error("usage: pages <dir> <zip..>");
  const archive = path.join(dir, "archive");
  fs.mkdirSync(archive, { recursive: true });
  const idOf = (f) => path.basename(f).replace(/-\d+\.\d+\.\d+[^/]*\.zip$/, "");
  const versionOf = (f) => path.basename(f).slice(idOf(f).length + 1, -".zip".length);
  const cmp = compareSemver;
  for (const zip of zips) fs.copyFileSync(zip, path.join(dir, path.basename(zip)));
  const byId = new Map();
  for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".zip"))) {
    byId.set(idOf(f), [...(byId.get(idOf(f)) || []), f]);
  }
  for (const [id, files] of byId) {
    files.sort((x, y) => cmp(versionOf(x), versionOf(y)));
    for (const old of files.slice(0, -1)) {
      fs.renameSync(path.join(dir, old), path.join(archive, old));
      console.log(`archived ${old}`);
    }
    console.log(`${id}: ${files.at(-1)}`);
  }
  fs.writeFileSync(path.join(dir, ".nojekyll"), "");
  blender("server-generate", `--repo-dir=${dir}`, "--html");
}

const [cmd, ...args] = process.argv.slice(2);
const commands = {
  sync,
  check,
  validate,
  build: () => build(args),
  release: () => release(args.length ? args : JSON.parse(process.env.PUBLISHED || "[]").map((p) => `${p.name}@${p.version}`)),
  pages: () => pages(args[0], args.slice(1)),
};
if (!commands[cmd]) {
  console.error("usage: blender-addons.mjs sync | check | validate | build [name..] | release [tag..] | pages <dir> <zip..>");
  process.exit(1);
}
commands[cmd]();
