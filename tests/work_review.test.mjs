import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { review } from '../scripts/work_review.mjs';
import { verifyBuild } from '../scripts/agent_note_source.mjs';
import { brief } from '../vendor/agent-note/src/service.mjs';
import { structuredRunner } from '../vendor/agent-note/test/model-fixture.mjs';

test('ported workflow retains upstream index, dossier, provenance, cache and stale behavior', async t => {
  await verifyBuild();
  const root = await fs.mkdtemp(path.join(os.tmpdir(), '2hours-work-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const sources = path.join(root, 'codex');
  await fs.mkdir(sources);
  const transcript = path.join(sources, 'session.jsonl');
  await fs.writeFile(transcript, [
    { type: 'session_meta', payload: { id: 'session-1', cwd: root } },
    { type: 'response_item', timestamp: '2026-08-29T01:00:00Z', payload: { type: 'message', role: 'user', content: [{ type: 'input_text', text: '重构 Today 后端。' }] } },
    { type: 'response_item', timestamp: '2026-08-29T01:10:00Z', payload: { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: '实现结构化 workflow。' }] } }
  ].map(JSON.stringify).join('\n') + '\n');
  await fs.utimes(transcript, new Date('2026-08-29T02:00:00Z'), new Date('2026-08-29T02:00:00Z'));
  const options = { date: '2026-08-29', timezone: 'Asia/Shanghai', source: 'codex', roots: [sources], dataDir: path.join(root, 'data') };
  let calls = 0;
  const fixture = structuredRunner();
  const runner = request => { calls++; return fixture(request); };
  const index = await review(options, { runner });
  assert.equal(calls, 2, 'uses the original digest and synthesis stages');
  assert.equal(index.index.schema, 'today-workline-index/v1');
  const original = await brief({ ...options, readOnly: true }, { runner: () => assert.fail('cache read called a model') });
  assert.deepEqual(index.index, original.index);
  assert.deepEqual(index.evidenceCoverage, original.evidenceCoverage);
  const workline = index.index.worklines[0].worklineId;
  const deep = await review({ ...options, workline }, { runner });
  assert.equal(calls, 5, 'uses the original analysis, critique and compose stages');
  const originalDeep = await brief({ ...options, workline, readOnly: true }, { runner: () => assert.fail('cache read called a model') });
  assert.deepEqual(deep.dossier, originalDeep.dossier);
  assert.equal(deep.dossier.sourceIndex.contentHash, index.index.contentHash);

  const script = fileURLToPath(new URL('../scripts/work_review.mjs', import.meta.url));
  const args = [script, 'brief', '--date', options.date, '--timezone', options.timezone, '--source', options.source, '--root', sources, '--data-dir', options.dataDir, '--read-only'];
  const cli = () => spawnSync(process.execPath, args, { cwd: os.tmpdir(), encoding: 'utf8', timeout: 20000 });
  let result = cli();
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).index, index.index, 'works from a different cwd without the installed agent-note CLI');
  const relocated = path.join(root, 'relocated install with spaces');
  await fs.mkdir(path.join(relocated, 'scripts'), { recursive: true });
  const project = fileURLToPath(new URL('../', import.meta.url));
  for (const name of ['agent_note_source.mjs', 'sync_agent_note.mjs', 'work_review.mjs']) await fs.copyFile(path.join(project, 'scripts', name), path.join(relocated, 'scripts', name));
  await fs.cp(path.join(project, 'vendor/agent-note'), path.join(relocated, 'vendor/agent-note'), { recursive: true, verbatimSymlinks: true });
  const moved = spawnSync(process.execPath, [path.join(relocated, 'scripts/work_review.mjs'), ...args.slice(1)], { cwd: os.tmpdir(), encoding: 'utf8', timeout: 20000 });
  assert.equal(moved.status, 0, moved.stderr);
  assert.deepEqual(JSON.parse(moved.stdout).index, index.index, 'a relocated installation resolves only its own source, build and dependencies');
  const movedSync = spawnSync(process.execPath, [path.join(relocated, 'scripts/sync_agent_note.mjs'), '--check'], { cwd: os.tmpdir(), encoding: 'utf8', timeout: 20000 });
  assert.equal(movedSync.status, 0, movedSync.stderr);
  assert.equal(JSON.parse(movedSync.stdout).checked, true, 'updater also runs correctly through canonicalized paths');
  await fs.appendFile(transcript, JSON.stringify({ type: 'response_item', timestamp: '2026-08-29T01:20:00Z', payload: { type: 'message', role: 'user', content: [{ type: 'input_text', text: '新的证据。' }] } }) + '\n');
  await fs.utimes(transcript, new Date('2026-08-29T02:00:00Z'), new Date('2026-08-29T02:00:00Z'));
  result = cli();
  assert.equal(result.status, 2, result.stderr);
  assert.equal(JSON.parse(result.stdout).mode, 'stale');
  assert.equal(calls, 5);
  const rejected = spawnSync(process.execPath, [script, 'brief', '--date', options.date, '--read-only', '--refresh'], { encoding: 'utf8' });
  assert.equal(rejected.status, 1);
  assert.match(rejected.stderr, /cannot be combined/);
  await assert.rejects(review({ ...options, signal: AbortSignal.abort() }), { name: 'AbortError' });
});

test('CLI entry points run when the loader path differs from the canonical path', async t => {
  // Windows CI expands 8.3 short names (RUNNER~1) in fs.realpath but not in import.meta.url.
  // A symlinked install run with --preserve-symlinks-main gives the same mismatch on every platform.
  const root = await fs.mkdtemp(path.join(os.tmpdir(), '2hours-entry-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const install = path.join(root, 'install with spaces');
  await fs.mkdir(path.join(install, 'scripts'), { recursive: true });
  const project = fileURLToPath(new URL('../', import.meta.url));
  for (const name of ['agent_note_source.mjs', 'sync_agent_note.mjs', 'work_review.mjs']) await fs.copyFile(path.join(project, 'scripts', name), path.join(install, 'scripts', name));
  const link = path.join(root, 'linked install');
  try { await fs.symlink(install, link, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (error.code === 'EPERM') return t.skip('symlinks are not permitted here'); throw error; }
  const run = (name, ...args) => spawnSync(process.execPath, ['--preserve-symlinks-main', path.join(link, 'scripts', name), ...args], { cwd: os.tmpdir(), encoding: 'utf8', timeout: 20000 });
  const help = run('work_review.mjs', '--help');
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /Two Hours · bundled Agent Note workflow/, 'work_review must not exit 0 without running');
  const sync = run('sync_agent_note.mjs', '--ref', 'HEAD');
  assert.equal(sync.status, 1, 'sync_agent_note must run and reject --ref without --repo');
  assert.match(sync.stderr, /--ref requires --repo/);
});
