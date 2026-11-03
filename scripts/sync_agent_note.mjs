import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { vendor, verifySource, sha256, sourcePath } from './agent_note_source.mjs';

const repository = 'https://github.com/WdBlink/agent-note-cli';
const generatedBackend = 'dist/backend.mjs';

export async function snapshot(repo, ref) {
  const git = args => execFileSync('git', ['-C', repo, ...args], { maxBuffer: 16 * 1024 * 1024 });
  const origin = git(['remote', 'get-url', 'origin']).toString().trim();
  if (!/^(?:https:\/\/github\.com\/|git@github\.com:)WdBlink\/agent-note-cli(?:\.git)?$/i.test(origin)) throw new Error('Expected the agent-note-cli upstream repository.');
  const commit = git(['rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`]).toString().trim();
  const entries = new Map(git(['ls-tree', '-rz', commit]).toString().split('\0').filter(Boolean).map(line => {
    const [metadata, name] = line.split('\t');
    return [name, metadata.split(' ')[0]];
  }));
  const files = new Map();
  function read(name) {
    sourcePath(repo, name);
    if (!['100644', '100755'].includes(entries.get(name))) throw new Error(`Missing regular upstream file: ${name}`);
    return git(['show', `${commit}:${name}`]);
  }
  function include(name) {
    if (files.has(name)) return;
    files.set(name, read(name));
    if (name.endsWith('.mjs')) {
      const imports = /(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)["'](\.[^"']+)["']/g;
      for (const match of files.get(name).toString().matchAll(imports)) {
        const dependency = path.posix.normalize(path.posix.join(path.posix.dirname(name), match[1]));
        if (dependency !== generatedBackend) include(dependency);
      }
    }
  }
  for (const name of ['package.json', 'package-lock.json', 'LICENSE', 'scripts/build.mjs', 'src/service.mjs', 'test/model-fixture.mjs']) include(name);
  for (const name of entries.keys()) if (name.startsWith('backend/')) include(name);
  const provenance = JSON.parse(files.get('backend/provenance.json'));
  for (const [name, hash] of Object.entries(provenance.files)) {
    const bytes = files.get(`backend/upstream/${name}`);
    if (!bytes || sha256(bytes) !== hash) throw new Error(`Upstream backend provenance mismatch: ${name}`);
  }
  const manifest = {
    repository, ref, commit, version: JSON.parse(files.get('package.json')).version,
    entry: 'src/service.mjs',
    files: Object.fromEntries([...files].sort(([a], [b]) => a.localeCompare(b, 'en')).map(([name, bytes]) => [name, sha256(bytes)]))
  };
  return { manifest, files };
}

async function replaceSource(repo, ref, destination, check) {
  destination = path.resolve(destination);
  const exists = await fs.lstat(destination).then(() => true, error => {
    if (error.code === 'ENOENT') return false;
    throw error;
  });
  const previous = exists ? await verifySource(destination) : null;
  if (!repo) {
    if (!check || !previous) throw new Error('Specify --repo PATH and --ref TAG_OR_COMMIT to sync.');
    return previous;
  }
  const next = await snapshot(path.resolve(repo), ref || previous?.commit || 'HEAD');
  const same = previous && previous.commit === next.manifest.commit && JSON.stringify(previous.files) === JSON.stringify(next.manifest.files);
  if (check) {
    if (!same) throw new Error('Vendored source does not match the requested upstream revision.');
    return previous;
  }
  if (same) return previous;
  await fs.mkdir(path.dirname(destination), { recursive: true });
  const stage = await fs.mkdtemp(path.join(path.dirname(destination), '.agent-note-stage-'));
  const backup = stage + '-previous';
  let dependenciesMoved = false;
  let oldMoved = false;
  try {
    for (const [name, bytes] of next.files) {
      const target = sourcePath(stage, name);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, bytes);
    }
    await fs.writeFile(path.join(stage, 'SOURCE.json'), JSON.stringify(next.manifest, null, 2) + '\n');
    await verifySource(stage);
    if (previous?.files['package-lock.json'] === next.manifest.files['package-lock.json']) {
      try {
        await fs.rename(path.join(destination, 'node_modules'), path.join(stage, 'node_modules'));
        dependenciesMoved = true;
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    if (previous) { await fs.rename(destination, backup); oldMoved = true; }
    await fs.rename(stage, destination);
  } catch (error) {
    const old = oldMoved ? backup : destination;
    if (dependenciesMoved) await fs.rename(path.join(stage, 'node_modules'), path.join(old, 'node_modules'));
    if (oldMoved) await fs.rename(backup, destination);
    throw error;
  } finally { await fs.rm(stage, { recursive: true, force: true }); }
  if (oldMoved) await fs.rm(backup, { recursive: true });
  return next.manifest;
}

export async function sync(repo, ref, destination = vendor, check = false) {
  destination = path.resolve(destination);
  if (check) return replaceSource(repo, ref, destination, true);
  await fs.mkdir(path.dirname(destination), { recursive: true });
  const lock = destination + '.sync-lock';
  try { await fs.mkdir(lock); }
  catch (error) { if (error.code === 'EEXIST') throw new Error(`Another sync may be active: ${lock}`); throw error; }
  try { return await replaceSource(repo, ref, destination, false); }
  finally { await fs.rmdir(lock); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(await fs.realpath(process.argv[1])).href) {
  try {
    const { values } = parseArgs({ options: { repo: { type: 'string' }, ref: { type: 'string' }, dest: { type: 'string' }, check: { type: 'boolean' } } });
    if (values.ref && !values.repo) throw new Error('--ref requires --repo.');
    const result = await sync(values.repo, values.ref, values.dest, values.check);
    console.log(JSON.stringify({ repository: result.repository, commit: result.commit, version: result.version, files: Object.keys(result.files).length, checked: Boolean(values.check) }));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
