import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const vendor = fileURLToPath(new URL('../vendor/agent-note/', import.meta.url));
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

export function sourcePath(root, name) {
  if (!name || name.includes('\\') || path.posix.isAbsolute(name) || name.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error(`Invalid snapshot path: ${name}`);
  }
  return path.join(root, name);
}

export async function filesIn(root, excluded = []) {
  const files = [];
  async function walk(directory, prefix = '') {
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      const name = prefix + entry.name;
      if (!prefix && excluded.includes(name)) continue;
      if (entry.isSymbolicLink()) throw new Error(`Snapshot symlink is not supported: ${name}`);
      if (entry.isDirectory()) await walk(path.join(directory, entry.name), name + '/');
      else if (entry.isFile()) files.push(name);
      else throw new Error(`Unsupported snapshot entry: ${name}`);
    }
  }
  await walk(root);
  return files.sort();
}

export async function verifySource(root = vendor) {
  if ((await fs.lstat(root)).isSymbolicLink()) throw new Error('Snapshot root must not be a symlink.');
  const raw = await fs.readFile(path.join(root, 'SOURCE.json'));
  const manifest = JSON.parse(raw);
  const actual = await filesIn(root, ['SOURCE.json', 'node_modules', 'dist', 'BUILD.json']);
  if (JSON.stringify(actual) !== JSON.stringify(Object.keys(manifest.files).sort())) throw new Error('Unrecorded or missing files in agent-note snapshot.');
  for (const [name, hash] of Object.entries(manifest.files)) {
    if (sha256(await fs.readFile(sourcePath(root, name))) !== hash) throw new Error(`Agent Note source drift: ${name}. Sync from upstream instead of editing the snapshot.`);
  }
  return { ...manifest, sourceHash: sha256(raw) };
}

export async function verifyBuild(root = vendor) {
  const source = await verifySource(root);
  const build = JSON.parse(await fs.readFile(path.join(root, 'BUILD.json')));
  if (build.sourceHash !== source.sourceHash) throw new Error('Agent Note build is stale. Run work_review.mjs --build.');
  const outputs = await filesIn(path.join(root, 'dist'));
  if (JSON.stringify(outputs) !== JSON.stringify(Object.keys(build.outputs).sort())) throw new Error('Agent Note build outputs changed. Rebuild the workflow.');
  for (const [name, hash] of Object.entries(build.outputs)) {
    if (sha256(await fs.readFile(sourcePath(path.join(root, 'dist'), name))) !== hash) throw new Error(`Agent Note build drift: ${name}.`);
  }
  return source;
}
