import { spawn } from 'node:child_process';
import { run } from './python.mjs';
const backend=run(['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000']);
const frontend=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1',...process.argv.slice(2)],{stdio:'inherit',windowsHide:true});
let stopping=false;
function stop(code=0) { if(stopping)return;stopping=true;backend.kill();frontend.kill();process.exitCode=code; }
backend.on('exit',code=>stop(code??1));frontend.on('exit',code=>stop(code??1));
frontend.on('error',()=>stop(1));backend.on('error',()=>stop(1));
process.on('SIGINT',()=>stop());process.on('SIGTERM',()=>stop());
