import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
const venv = resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
export const python = existsSync(venv) ? venv : 'python';
export function run(args) {
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
