// Run the installed-package project acceptance against the supplied wheel interpreter.
// The old implicit-start smoke is superseded by explicit project setup/admission.
import {spawnSync} from 'node:child_process';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const [executable,browser='chromium',...extra]=process.argv.slice(2);
if(extra.length||!executable||!['chromium','firefox','webkit'].includes(browser))throw Error('Usage: studio-smoke.mjs <installed-simulatte> [chromium|firefox|webkit]');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const result=spawnSync(process.platform==='win32'?'pnpm.cmd':'pnpm',['exec','playwright','test','browser/project.spec.ts','browser/project-history.spec.ts','browser/project-notifications.spec.ts','browser/project-adapters.spec.ts','browser/workspace.spec.ts','browser/journey.spec.ts','browser/analysis.spec.ts','--project',browser],{cwd:root,stdio:'inherit',env:{...process.env,SIMULATTE_PROJECT_EXECUTABLE:resolve(executable)}});
if(result.error)throw result.error;
process.exitCode=result.status??1;
