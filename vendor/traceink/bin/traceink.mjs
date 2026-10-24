#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync
} from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const PACKAGE_VERSION = "0.2.0";
const SKILL_NAME = "traceink";
const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const args = process.argv.slice(2);
const allowedOptions = new Set(["--force", "--help", "--version", "--agent"]);
const unknownOption = args.find((arg) => {
  if (!arg.startsWith("-")) return false;
  if (arg.startsWith("--agent=")) return false;
  return !allowedOptions.has(arg);
});

if (unknownOption) {
  console.error(`Unknown option: ${unknownOption}`);
  printHelp();
  process.exit(2);
}

const agentOptionIndex = args.indexOf("--agent");
if (agentOptionIndex !== -1 && (!args[agentOptionIndex + 1] || args[agentOptionIndex + 1].startsWith("-"))) {
  console.error("--agent requires a value: codex or copilot.");
  printHelp();
  process.exit(2);
}

const agentEqualsOption = args.find((arg) => arg.startsWith("--agent="));
if (agentOptionIndex !== -1 && agentEqualsOption) {
  console.error("Specify --agent only once.");
  printHelp();
  process.exit(2);
}

const agent = (
  agentEqualsOption?.slice("--agent=".length) ??
  (agentOptionIndex === -1 ? "codex" : args[agentOptionIndex + 1])
).trim().toLowerCase();
if (!["codex", "copilot"].includes(agent)) {
  console.error(`Unsupported agent: ${agent}. Expected codex or copilot.`);
  printHelp();
  process.exit(2);
}

const positional = args.filter((arg, index) => {
  if (arg.startsWith("-")) return false;
  return index !== agentOptionIndex + 1;
});
const command = positional[0] ?? "install";
const force = args.includes("--force");

if (args.includes("--help") || command === "help") {
  printHelp();
  process.exit(0);
}

if (args.includes("--version") || command === "version") {
  console.log(PACKAGE_VERSION);
  process.exit(0);
}

if (command !== "install") {
  console.error(`Unknown command: ${command}`);
  printHelp();
  process.exit(2);
}

if (positional.length > 1) {
  console.error(`Unexpected argument: ${positional[1]}`);
  printHelp();
  process.exit(2);
}

const homeVariable = agent === "copilot" ? "COPILOT_HOME" : "CODEX_HOME";
const defaultHome = path.join(os.homedir(), agent === "copilot" ? ".copilot" : ".codex");
const agentHome = process.env[homeVariable]?.trim() || defaultHome;
if (!path.isAbsolute(agentHome)) {
  console.error(`${homeVariable} must be an absolute path: ${agentHome}`);
  process.exit(1);
}
if (path.resolve(agentHome) === path.parse(path.resolve(agentHome)).root) {
  console.error(`${homeVariable} cannot be the filesystem root.`);
  process.exit(1);
}
const target = path.join(agentHome, "skills", SKILL_NAME);
let prepared;

try {
  prepared = prepareSource(agent);
  installSkill({ source: prepared.source, target, force });
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
} finally {
  prepared?.cleanup();
}

function installSkill({ source, target, force }) {
  if (!existsSync(path.join(source, "SKILL.md"))) {
    throw new Error("Package is missing skill/SKILL.md; installation aborted.");
  }

  const sourceFingerprint = fingerprint(source);
  const targetEntry = statEntry(target);
  if (targetEntry?.isSymbolicLink()) {
    try {
      if (sourceFingerprint === fingerprint(target)) {
        console.log(`Traceink is already up to date at ${target}`);
        return;
      }
    } catch {
      // Broken or unsafe links are handled by the refusal below.
    }
    throw new Error(`The existing target is a symbolic link: ${target}. Remove or update it manually; --force will not replace links.`);
  }

  if (targetEntry && !targetEntry.isDirectory()) {
    throw new Error(`The existing target is not a directory: ${target}. Remove it manually before installing.`);
  }

  if (targetEntry) {
    if (sourceFingerprint === fingerprint(target)) {
      console.log(`Traceink is already up to date at ${target}`);
      return;
    }
    if (!force) {
      throw new Error(`A different installation already exists at ${target}. Re-run with --force to replace it.`);
    }
  }

  const parent = path.dirname(target);
  mkdirSync(parent, { recursive: true });
  const stageRoot = mkdtempSync(path.join(parent, `.${SKILL_NAME}-stage-`));
  const stage = path.join(stageRoot, "payload");
  let backupRoot = null;
  let backup = null;

  try {
    cpSync(source, stage, { recursive: true, force: false, errorOnExist: true });
    if (fingerprint(stage) !== sourceFingerprint) {
      throw new Error("The staged skill failed verification; installation aborted.");
    }

    const currentTargetEntry = statEntry(target);
    if (!targetEntry && currentTargetEntry) {
      if (currentTargetEntry.isDirectory() && fingerprint(target) === sourceFingerprint) {
        rmSync(stageRoot, { recursive: true, force: true });
        console.log(`Traceink is already up to date at ${target}`);
        return;
      }
      throw new Error(`The target changed during installation: ${target}. Nothing was replaced; run the command again.`);
    }
    if (targetEntry && (!currentTargetEntry || !sameEntry(targetEntry, currentTargetEntry))) {
      throw new Error(`The target changed during installation: ${target}. Nothing was replaced; run the command again.`);
    }

    if (targetEntry) {
      backupRoot = mkdtempSync(path.join(parent, `.${SKILL_NAME}-backup-`));
      backup = path.join(backupRoot, "payload");
      renameSync(target, backup);
    }
    renameSync(stage, target);
    rmSync(stageRoot, { recursive: true, force: true });
    if (backupRoot) rmSync(backupRoot, { recursive: true, force: true });
  } catch (error) {
    rmSync(stageRoot, { recursive: true, force: true });
    if (!statEntry(target) && backup && statEntry(backup)) {
      try {
        renameSync(backup, target);
      } catch (recoveryError) {
        throw new Error(`${messageOf(error)} Recovery failed; the previous installation remains at ${backup}. ${messageOf(recoveryError)}`);
      }
    }
    if (backupRoot && !statEntry(backup)) rmSync(backupRoot, { recursive: true, force: true });
    throw error;
  }

  console.log(`Installed Traceink to ${target}`);
  console.log(agent === "copilot"
    ? "Invoke it in Copilot CLI with: /traceink 回看本周"
    : "Invoke it in Codex with: $traceink 回看本周");
}

function prepareSource(selectedAgent) {
  if (selectedAgent === "codex") {
    return {
      source: path.join(packageRoot, "skill"),
      cleanup() {}
    };
  }

  const root = mkdtempSync(path.join(os.tmpdir(), `${SKILL_NAME}-copilot-source-`));
  try {
    const payload = path.join(root, "payload");
    cpSync(path.join(packageRoot, "copilot"), payload, { recursive: true });
    cpSync(path.join(packageRoot, "skill"), path.join(payload, "base"), { recursive: true });
    return {
      source: payload,
      cleanup() {
        rmSync(root, { recursive: true, force: true });
      }
    };
  } catch (error) {
    rmSync(root, { recursive: true, force: true });
    throw error;
  }
}

function sameEntry(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.isDirectory() === right.isDirectory();
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error);
}

function fingerprint(root) {
  const hash = createHash("sha256");
  for (const relative of listFiles(root)) {
    hash.update(relative);
    hash.update("\0");
    hash.update(readFileSync(path.join(root, relative)));
    hash.update("\0");
  }
  return hash.digest("hex");
}

function listFiles(root, prefix = "") {
  const rows = [];
  for (const entry of readdirSync(path.join(root, prefix), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const relative = path.join(prefix, entry.name);
    if (entry.isDirectory()) rows.push(...listFiles(root, relative));
    else if (entry.isFile()) rows.push(relative);
    else if (entry.isSymbolicLink()) throw new Error(`Refusing to fingerprint a tree containing a symbolic link: ${relative}`);
    else throw new Error(`Refusing to fingerprint an unsupported filesystem entry: ${relative}`);
  }
  return rows;
}

function statEntry(value) {
  try {
    return lstatSync(value);
  } catch (error) {
    if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") return null;
    throw error;
  }
}

function printHelp() {
  console.log(`Traceink ${PACKAGE_VERSION}

Usage:
  traceink install [--agent codex|copilot] [--force]

Installs for Codex by default.
Codex target:   \${CODEX_HOME:-~/.codex}/skills/${SKILL_NAME}
Copilot target: \${COPILOT_HOME:-~/.copilot}/skills/${SKILL_NAME}
Use --force only when you intend to replace a different local copy.`);
}
