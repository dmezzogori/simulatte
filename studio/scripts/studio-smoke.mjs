// Run the installed-package project acceptance against the supplied wheel interpreter.
// The old implicit-start smoke is superseded by explicit project setup/admission.
import {spawnSync} from 'node:child_process';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const [executable,browser='chromium']=process.argv.slice(2);
if(!executable||!['chromium','firefox','webkit'].includes(browser))throw Error('Usage: studio-smoke.mjs <installed-simulatte> [chromium|firefox|webkit]');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const result=spawnSync(process.platform==='win32'?'pnpm.cmd':'pnpm',['exec','playwright','test','browser/project.spec.ts','--project',browser],{cwd:root,stdio:'inherit',env:{...process.env,SIMULATTE_PROJECT_EXECUTABLE:resolve(executable)}});
if(result.error)throw result.error;
process.exitCode=result.status??1;
