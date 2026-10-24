import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const cliPath = path.join(repoRoot, "bin", "traceink.mjs");

test("reports the package version", () => {
  const result = spawnSync(process.execPath, [cliPath, "--version"], {
    cwd: repoRoot,
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "0.2.0");
});

test("installs the complete skill into CODEX_HOME", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-install-"));
  const result = run(["install"], codexHome);

  assert.equal(result.status, 0, result.stderr);
  const target = path.join(codexHome, "skills", "traceink");
  assert.match(await readFile(path.join(target, "SKILL.md"), "utf8"), /name: traceink/);
  assert.match(await readFile(path.join(target, "references", "editorial-contract.md"), "utf8"), /Evidence-led review contract/);
  assert.match(await readFile(path.join(target, "agents", "openai.yaml"), "utf8"), /display_name: "Traceink"/);
});

test("installs a Copilot adapter with an unchanged copy of the base skill", async () => {
  const copilotHome = await mkdtemp(path.join(os.tmpdir(), "traceink-copilot-install-"));
  const result = run(["install", "--agent", "copilot"], copilotHome, "COPILOT_HOME");

  assert.equal(result.status, 0, result.stderr);
  const target = path.join(copilotHome, "skills", "traceink");
  assert.match(await readFile(path.join(target, "SKILL.md"), "utf8"), /Traceink for GitHub Copilot CLI/);
  assert.equal(
    await readFile(path.join(target, "base", "SKILL.md"), "utf8"),
    await readFile(path.join(repoRoot, "skill", "SKILL.md"), "utf8")
  );
  assert.match(
    await readFile(path.join(target, "base", "references", "editorial-contract.md"), "utf8"),
    /Evidence-led review contract/
  );
});

test("Copilot session policy defaults local and requires explicit cloud opt-in", () => {
  const script = path.join(repoRoot, "copilot", "scripts", "session-source.mjs");

  const defaultResult = spawnSync(process.execPath, [script], {
    cwd: repoRoot,
    encoding: "utf8",
    env: without(process.env, "TRACEINK_COPILOT_CLOUD_SESSIONS")
  });
  assert.equal(defaultResult.status, 0, defaultResult.stderr);
  assert.equal(defaultResult.stdout.trim(), "local");

  const cloudResult = spawnSync(process.execPath, [script], {
    cwd: repoRoot,
    encoding: "utf8",
    env: { ...process.env, TRACEINK_COPILOT_CLOUD_SESSIONS: "1" }
  });
  assert.equal(cloudResult.status, 0, cloudResult.stderr);
  assert.equal(cloudResult.stdout.trim(), "cloud");
});

test("Copilot session policy rejects ambiguous values", () => {
  const script = path.join(repoRoot, "copilot", "scripts", "session-source.mjs");
  const result = spawnSync(process.execPath, [script], {
    cwd: repoRoot,
    encoding: "utf8",
    env: { ...process.env, TRACEINK_COPILOT_CLOUD_SESSIONS: "true" }
  });

  assert.equal(result.status, 2);
  assert.match(result.stderr, /must be 0 or 1/);
});

test("reinstalling identical content is idempotent", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-idempotent-"));
  assert.equal(run(["install"], codexHome).status, 0);

  const second = run(["install"], codexHome);
  assert.equal(second.status, 0, second.stderr);
  assert.match(second.stdout, /already up to date/i);
});

test("refuses to overwrite a different installation without force", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-refuse-"));
  const target = path.join(codexHome, "skills", "traceink");
  await mkdir(target, { recursive: true });
  await writeFile(path.join(target, "SKILL.md"), "user-owned version\n", "utf8");

  const result = run(["install"], codexHome);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /--force/);
  assert.equal(await readFile(path.join(target, "SKILL.md"), "utf8"), "user-owned version\n");
});

test("force replaces a different installation", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-force-"));
  const target = path.join(codexHome, "skills", "traceink");
  await mkdir(target, { recursive: true });
  await writeFile(path.join(target, "SKILL.md"), "old version\n", "utf8");

  const result = run(["install", "--force"], codexHome);
  assert.equal(result.status, 0, result.stderr);
  assert.match(await readFile(path.join(target, "SKILL.md"), "utf8"), /name: traceink/);
});

test("rejects unknown options instead of silently installing", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-option-"));
  const result = run(["install", "--bogus"], codexHome);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /unknown option/i);
});

test("rejects a relative CODEX_HOME", async () => {
  const relativeHome = `.relative-codex-home-${process.pid}`;
  try {
    const result = run(["install"], relativeHome);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /absolute path/i);
  } finally {
    await rm(path.join(repoRoot, relativeHome), { recursive: true, force: true });
  }
});

test("rejects a relative COPILOT_HOME", () => {
  const result = run(["install", "--agent=copilot"], `.relative-copilot-home-${process.pid}`, "COPILOT_HOME");
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /COPILOT_HOME must be an absolute path/i);
});

test("rejects an unsupported agent", () => {
  const result = run(["install", "--agent", "claude"], os.tmpdir());
  assert.equal(result.status, 2);
  assert.match(result.stderr, /unsupported agent/i);
});

test("an identical symlink installation is a read-only no-op", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-symlink-same-"));
  const skills = path.join(codexHome, "skills");
  await mkdir(skills, { recursive: true });
  await symlink(path.join(repoRoot, "skill"), path.join(skills, "traceink"));

  const result = run(["install"], codexHome);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /already up to date/i);
});

test("refuses to replace a different symlink even with force", async () => {
  const codexHome = await mkdtemp(path.join(os.tmpdir(), "traceink-symlink-other-"));
  const skills = path.join(codexHome, "skills");
  const other = await mkdtemp(path.join(os.tmpdir(), "traceink-other-source-"));
  await mkdir(skills, { recursive: true });
  await writeFile(path.join(other, "SKILL.md"), "other skill\n", "utf8");
  const target = path.join(skills, "traceink");
  await symlink(other, target);

  const result = run(["install", "--force"], codexHome);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /symbolic link/i);
  assert.equal(await readFile(path.join(target, "SKILL.md"), "utf8"), "other skill\n");
});

function run(args, agentHome, homeVariable = "CODEX_HOME") {
  return spawnSync(process.execPath, [cliPath, ...args], {
    cwd: repoRoot,
    encoding: "utf8",
    env: { ...process.env, [homeVariable]: agentHome, NO_COLOR: "1" }
  });
}

function without(source, key) {
  const copy = { ...source };
  delete copy[key];
  return copy;
}
