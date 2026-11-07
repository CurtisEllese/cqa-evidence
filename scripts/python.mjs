import { existsSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
const suffix = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python';
export const python = ['.venv311', '.venv'].map(dir => resolve(dir, suffix)).find(existsSync) || (process.platform === 'win32' ? 'python' : 'python3.11');
export function run(args) {
  const version = spawnSync(python, ['-c', 'import sys; print("%s.%s" % sys.version_info[:2])'], { encoding: 'utf8', windowsHide: true });
  if (version.status !== 0 || version.stdout.trim() !== '3.11') {
    throw new Error('Python 3.11 is required. Create .venv311 and install backend/requirements.txt.');
  }
  const child = spawn(python, args, { stdio: 'inherit', windowsHide: true });
  child.on('error', () => { console.error('Create .venv and install backend/requirements.txt first.'); process.exitCode=1; });
  return child;
}
if (process.argv[1] && resolve(process.argv[1]) === resolve('scripts/python.mjs')) {
  const child=run(process.argv.slice(2));
  child.on('exit', code => { process.exitCode=code ?? 1; });
  process.on('SIGINT', () => child.kill('SIGINT'));
  process.on('SIGTERM', () => child.kill('SIGTERM'));
}
