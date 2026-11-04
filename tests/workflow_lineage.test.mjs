import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { review } from '../scripts/work_review.mjs';
import { structuredRunner, writeWorkflowFixture, workflowRunner } from '../vendor/agent-note/test/model-fixture.mjs';

test('default Two Hours sources preserve Claude workflow, Codex evidence and frozen replay', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), '2hours-lineage-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const fixture = await writeWorkflowFixture(root);
  const options = { date: '2026-08-29', timezone: 'Asia/Shanghai', compiler: 'codex', roots: fixture.roots, dataDir: path.join(root, 'data') };
  let digests = 0;
  const runner = workflowRunner(structuredRunner(), request => {
    if (request.stdin.includes('Digest exactly one')) digests++;
  });
  const index = await review(options, { runner, scan: async (...args) => {
    assert.deepEqual(args[0].enabledSessionProviders, ['codex', 'claude']);
    const { loadAgentWorkSnapshot } = await import('../vendor/agent-note/dist/backend.mjs');
    return loadAgentWorkSnapshot(...args);
  } });
  assert.equal(digests, 1, 'one parent family enters the original upstream digest');
  assert.equal(index.index.worklines.length, 1);
  assert.deepEqual(index.index.worklines[0].evidenceIds, ['session:claude:session-1', 'family-child:codex:worker-1']);
  assert.equal(index.sessions.find(session => session.id === 'worker-1').lineage.parentProvider, 'claude');
  const workline = index.index.worklines[0].worklineId;
  const deep = await review({ ...options, workline }, { runner });
  assert.equal(deep.dossier.sourceIndex.contentHash, index.index.contentHash);
  const cli = spawnSync(process.execPath, ['scripts/work_review.mjs', 'brief', '--date', options.date, '--timezone', options.timezone,
    '--root', fixture.roots[0], '--root', fixture.roots[1], '--data-dir', options.dataDir, '--read-only'], { encoding: 'utf8' });
  assert.equal(cli.status, 0, cli.stderr);
  assert.deepEqual(JSON.parse(cli.stdout).index, index.index);
  const cached = await review({ ...options, readOnly: true }, { runner: () => assert.fail('cache called a model') });
  assert.deepEqual(cached.index, index.index);
});
