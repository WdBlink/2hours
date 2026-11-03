import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { vendor, verifySource, verifyBuild, filesIn, sha256 } from './agent_note_source.mjs';

const help = `Two Hours · bundled Agent Note workflow

  node scripts/work_review.mjs --build
  node scripts/work_review.mjs --check
  node scripts/work_review.mjs brief --date YYYY-MM-DD --timezone Asia/Shanghai
  node scripts/work_review.mjs brief --date YYYY-MM-DD --workline ID --read-only

  --source NAME                      Default: Codex + Claude; all opts into every supported source
  --compiler NAME                    Use an upstream-supported model CLI independently of source
  --root PATH                       Repeat for custom source directories
  --project PATH / --settings FILE   Upstream scan and model settings
  --data-dir PATH                    Default: ~/.2hours/agent-note
  --read-only / --refresh            Reuse only / explicitly regenerate

stdout contains agent-note-cli/view/v1 JSON; exit 2 preserves partial/stale results.
Generation and first deep read use the upstream configured model CLI.
`;

const expand = value => path.resolve(value === '~' ? os.homedir() : value.startsWith('~/') ? path.join(os.homedir(), value.slice(2)) : value);

export async function buildWorkflow() {
  const source = await verifySource();
  await fs.rm(path.join(vendor, 'BUILD.json'), { force: true });
  await fs.rm(path.join(vendor, 'dist'), { recursive: true, force: true });
  const result = spawnSync(process.execPath, ['scripts/build.mjs'], { cwd: vendor, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error('Upstream build failed. Run npm ci --prefix vendor/agent-note first.');
  const outputs = {};
  for (const name of await filesIn(path.join(vendor, 'dist'))) outputs[name] = sha256(await fs.readFile(path.join(vendor, 'dist', name)));
  await fs.writeFile(path.join(vendor, 'BUILD.json'), JSON.stringify({ sourceHash: source.sourceHash, outputs }, null, 2) + '\n');
}

export async function review(options, dependencies = {}) {
  if (options.timezone) {
    new Intl.DateTimeFormat('en', { timeZone: options.timezone }).format();
    process.env.TZ = options.timezone;
  }
  await verifyBuild();
  const { brief } = await import('../vendor/agent-note/src/service.mjs');
  const settings = { ...options.settings };
  if (!options.source && !settings.enabledSessionProviders) settings.enabledSessionProviders = ['codex', 'claude'];
  return brief({ ...options, settings, source: options.source ?? 'all', dataDir: options.dataDir ?? path.join(os.homedir(), '.2hours', 'agent-note') }, dependencies);
}

async function main() {
  const { values: v, positionals } = parseArgs({ allowPositionals: true, options: {
    build: { type: 'boolean' }, check: { type: 'boolean' }, help: { type: 'boolean', short: 'h' },
    date: { type: 'string' }, timezone: { type: 'string' }, source: { type: 'string' }, compiler: { type: 'string' },
    root: { type: 'string', multiple: true }, project: { type: 'string' }, settings: { type: 'string' },
    'data-dir': { type: 'string' }, workline: { type: 'string' },
    'read-only': { type: 'boolean' }, refresh: { type: 'boolean' }
  } });
  if (v.help) return void process.stdout.write(help);
  if (positionals.length > 1 || (positionals[0] && positionals[0] !== 'brief')) throw new Error('Expected brief, --build or --check.');
  if (v.build && v.check) throw new Error('--build and --check cannot be combined.');
  if (v.build) return buildWorkflow();
  if (v.check) {
    const source = await verifyBuild();
    await import('../vendor/agent-note/src/service.mjs');
    return console.log(JSON.stringify({ version: source.version, commit: source.commit, files: Object.keys(source.files).length, built: true }));
  }
  if (!v.date) throw new Error('--date is required; use the frozen review scope.');
  if (v['read-only'] && v.refresh) throw new Error('--read-only and --refresh cannot be combined.');
  let settings;
  if (v.settings) {
    const document = JSON.parse(await fs.readFile(expand(v.settings), 'utf8'));
    settings = document.settings ?? document;
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error('Settings must be an object.');
  }
  const controller = new AbortController();
  const interrupt = () => { process.exitCode = 130; controller.abort(); };
  const terminate = () => { process.exitCode = 143; controller.abort(); };
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', terminate);
  try {
    const view = await review({ date: v.date, timezone: v.timezone, source: v.source, compiler: v.compiler, roots: v.root?.map(expand), project: v.project && expand(v.project),
      settings, dataDir: v['data-dir'] && expand(v['data-dir']), workline: v.workline, readOnly: v['read-only'], refresh: v.refresh, signal: controller.signal,
      onProgress: progress => process.stderr.write(JSON.stringify(progress) + '\n') });
    process.stdout.write(JSON.stringify(view, null, 2) + '\n');
    if (view.mode === 'stale' || view.warnings.length || view.evidenceCoverage.some(e => ['failed', 'truncated'].includes(e.disposition)) || (view.index && !view.index.coverage.complete)) process.exitCode = 2;
  } finally {
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', terminate);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(await fs.realpath(process.argv[1])).href) {
  process.stdout.on('error', error => { if (error.code === 'EPIPE') process.exit(0); else throw error; });
  main().catch(error => { console.error(`work-review: ${error.message}`); process.exitCode ||= 1; });
}
