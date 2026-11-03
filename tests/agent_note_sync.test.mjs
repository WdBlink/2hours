import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { sync } from '../scripts/sync_agent_note.mjs';
import { sha256, verifySource, verifyBuild, sourcePath } from '../scripts/agent_note_source.mjs';

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), '2hours-sync-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const repo = path.join(root, 'upstream');
  const dest = path.join(root, 'ported');
  await fs.mkdir(repo);
  const git = (...args) => execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' }).trim();
  git('init', '-q');
  git('remote', 'add', 'origin', 'https://github.com/WdBlink/agent-note-cli.git');
  const files = {
    'package.json': '{"version":"0.6.0"}', 'package-lock.json': '{"lockfileVersion":3}', LICENSE: 'MIT fixture',
    'scripts/build.mjs': 'export {};', 'src/service.mjs': "export { core } from '../dist/backend.mjs';\nexport { saved } from './storage.mjs';",
    'src/storage.mjs': 'export const saved = true;', 'test/model-fixture.mjs': 'export const fixture = true;',
    'src/terminal.mjs': 'not part of the workflow', 'src/assets/demo.png': 'not copied',
    'backend/index.mjs': "export {core} from './upstream/core.ts';", 'backend/upstream/core.ts': 'export const core = 1;',
    'backend/provenance.json': JSON.stringify({ files: { 'core.ts': sha256('export const core = 1;') } })
  };
  async function write(name, content) { const file = path.join(repo, name); await fs.mkdir(path.dirname(file), { recursive: true }); await fs.writeFile(file, content); }
  for (const [name, content] of Object.entries(files)) await write(name, content);
  const commit = () => { git('add', '.'); git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'); return git('rev-parse', 'HEAD'); };
  return { root, repo, dest, git, write, commit, initial: commit() };
}

test('sync pins committed bytes, follows service imports, and excludes terminal assets', async t => {
  const f = await fixture(t);
  await f.write('src/service.mjs', 'uncommitted edits must stay upstream');
  const result = await sync(f.repo, f.initial, f.dest);
  assert.equal(result.commit, f.initial);
  assert.match(await fs.readFile(path.join(f.dest, 'src/service.mjs'), 'utf8'), /storage.mjs/);
  assert.equal(await fs.readFile(path.join(f.dest, 'src/storage.mjs'), 'utf8'), 'export const saved = true;');
  assert(!Object.hasOwn(result.files, 'src/terminal.mjs'));
  assert(!Object.hasOwn(result.files, 'src/assets/demo.png'));
  await sync(f.repo, f.initial, f.dest, true);
  const before = await fs.readFile(path.join(f.dest, 'SOURCE.json'));
  await sync(f.repo, f.initial, f.dest);
  assert.deepEqual(await fs.readFile(path.join(f.dest, 'SOURCE.json')), before);
});

test('updates remove retired modules, retain matching dependencies, and invalidate old builds', async t => {
  const f = await fixture(t);
  await sync(f.repo, f.initial, f.dest);
  await fs.mkdir(path.join(f.dest, 'node_modules'));
  await fs.writeFile(path.join(f.dest, 'node_modules/keep'), 'installed');
  await fs.mkdir(path.join(f.dest, 'dist'));
  await fs.writeFile(path.join(f.dest, 'dist/backend.mjs'), 'old build');
  await fs.writeFile(path.join(f.dest, 'BUILD.json'), '{}');
  await f.write('src/service.mjs', "export {core} from '../dist/backend.mjs';\nexport {next} from './next.mjs';");
  await f.write('src/next.mjs', 'export const next = true;');
  await fs.unlink(path.join(f.repo, 'src/storage.mjs'));
  const next = f.commit();
  await assert.rejects(sync(f.repo, next, f.dest, true), /does not match/);
  await sync(f.repo, next, f.dest);
  await assert.rejects(fs.access(path.join(f.dest, 'src/storage.mjs')), { code: 'ENOENT' });
  assert.equal(await fs.readFile(path.join(f.dest, 'src/next.mjs'), 'utf8'), 'export const next = true;');
  assert.equal(await fs.readFile(path.join(f.dest, 'node_modules/keep'), 'utf8'), 'installed');
  await assert.rejects(fs.access(path.join(f.dest, 'BUILD.json')), { code: 'ENOENT' });
  await assert.rejects(fs.access(path.join(f.dest, 'dist')), { code: 'ENOENT' });
  await f.write('package-lock.json', '{"lockfileVersion":3,"changed":true}');
  await sync(f.repo, f.commit(), f.dest);
  await assert.rejects(fs.access(path.join(f.dest, 'node_modules')), { code: 'ENOENT' });
});

test('source edits, unknown files, and unmanaged destinations are preserved and rejected', async t => {
  const f = await fixture(t);
  await sync(f.repo, f.initial, f.dest);
  await fs.writeFile(path.join(f.dest, 'src/storage.mjs'), 'local change');
  await assert.rejects(sync(f.repo, f.initial, f.dest), /source drift/);
  assert.equal(await fs.readFile(path.join(f.dest, 'src/storage.mjs'), 'utf8'), 'local change');
  await fs.writeFile(path.join(f.dest, 'src/storage.mjs'), 'export const saved = true;');
  await fs.writeFile(path.join(f.dest, 'private-run.json'), 'do not remove');
  await assert.rejects(sync(f.repo, f.initial, f.dest), /Unrecorded/);
  assert.equal(await fs.readFile(path.join(f.dest, 'private-run.json'), 'utf8'), 'do not remove');
  const unmanaged = path.join(f.root, 'unmanaged');
  await fs.mkdir(unmanaged);
  await fs.writeFile(path.join(unmanaged, 'keep'), 'keep');
  await assert.rejects(sync(f.repo, f.initial, unmanaged));
  assert.equal(await fs.readFile(path.join(unmanaged, 'keep'), 'utf8'), 'keep');
  assert.throws(() => sourcePath(f.root, '../outside'), /Invalid snapshot path/);
});

test('checks reject altered build output and changed upstream provenance', async t => {
  const f = await fixture(t);
  await sync(f.repo, f.initial, f.dest);
  const source = await verifySource(f.dest);
  await fs.mkdir(path.join(f.dest, 'dist'));
  await fs.writeFile(path.join(f.dest, 'dist/backend.mjs'), 'built');
  await fs.writeFile(path.join(f.dest, 'BUILD.json'), JSON.stringify({ sourceHash: source.sourceHash, outputs: { 'backend.mjs': sha256('built') } }));
  await verifyBuild(f.dest);
  await fs.writeFile(path.join(f.dest, 'dist/backend.mjs'), 'tampered');
  await assert.rejects(verifyBuild(f.dest), /build drift/);
  await f.write('backend/upstream/core.ts', 'export const core = 2;');
  await assert.rejects(sync(f.repo, f.commit(), f.dest), /provenance mismatch/);
  assert.equal((await verifySource(f.dest)).commit, f.initial);
});

test('competing updates and the wrong upstream cannot replace the source', async t => {
  const f = await fixture(t);
  await sync(f.repo, f.initial, f.dest);
  await fs.mkdir(f.dest + '.sync-lock');
  await assert.rejects(sync(f.repo, f.initial, f.dest), /sync may be active/);
  await fs.rmdir(f.dest + '.sync-lock');
  f.git('remote', 'set-url', 'origin', 'https://github.com/example/other.git');
  await assert.rejects(sync(f.repo, f.initial, f.dest), /upstream repository/);
  assert.equal((await verifySource(f.dest)).commit, f.initial);
});
