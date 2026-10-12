import {chromium, firefox, webkit, expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';

// Executable may be wheel-installed. All model files and process state are isolated.
// Never forward captured CLI output or navigation errors containing bootstrap URLs.
const [executable, browserName = 'chromium'] = process.argv.slice(2);
const engine = {chromium, firefox, webkit}[browserName];
if (!engine) throw new Error('Unknown browser');
if (!executable) throw new Error('Usage: node studio-smoke.mjs <simulatte-executable> [chromium|firefox|webkit]');
const root = await mkdtemp(join(tmpdir(), 'simulatte-live-smoke-'));
await writeFile(join(root, 'pyproject.toml'), '[tool.simulatte.bundle]\ninclude = ["plant.py"]\n');
await writeFile(join(root, 'plant.py'), `import time
from dataclasses import dataclass
from simulatte import model
@dataclass(frozen=True)
class Params:
    delay: float = 0.05
@model(params=Params, horizon=4)
def build(env, layout, params):
    def work():
        for _ in range(4):
            time.sleep(params.delay)
            yield env.timeout(1)
    env.process(work())
`);
const child = spawn(resolve(executable), ['studio', join(root, 'plant.py'), '--no-browser', '--port', '0'], {
  cwd: root, env: {...process.env, PYTHONUNBUFFERED: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
});
const exited = new Promise(done => child.once('close', done));
let diagnostic = "";
child.stderr.on("data", data => {diagnostic = (diagnostic + data.toString()).slice(-8192);});
let browser;
let stage = 'startup';
const watchdog = setTimeout(() => {child.kill('SIGINT');}, 90000);
try {
  const link = await new Promise((done, reject) => {
    let buffer = '';
    const timer = setTimeout(() => reject(new Error('Startup timeout')), 20000);
    child.once('error', () => {clearTimeout(timer); reject(new Error('Startup failed'));});
    child.once('exit', () => {clearTimeout(timer); reject(new Error('Early exit'));});
    child.stdout.on('data', data => {
      buffer = (buffer + data.toString()).slice(-65536);
      const found = buffer.match(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/);
      if (found) {clearTimeout(timer); child.stdout.removeAllListeners('data'); child.stdout.resume(); buffer = ''; done(found[0]);}
    });
  });
  browser = await engine.launch();
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  let errors = 0;
  page.on('pageerror', () => errors++);
  stage = 'bootstrap';
  await page.goto(link);
  await expect(page.getByRole('region', {name: 'Studio execution'}).or(page.locator('.studio-controls'))).toBeVisible();
  await expect(page.locator('.studio-controls [role=status]')).toHaveText('connected', {timeout: 20000});
  stage = 'automatic execution';
  await expect(page.locator('.run-title strong')).toContainText('Run · revision', {timeout: 20000});
  const before = await page.locator('.run-title strong').textContent();
  stage = 'reload';
  await page.reload();
  await expect(page.locator('.studio-controls [role=status]')).toHaveText('connected', {timeout: 20000});
  await expect(page.locator('.run-title strong')).toHaveText(before, {timeout: 20000});
  stage = 'manual execution';
  await page.getByRole('button', {name: 'Run', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Run', exact: true})).toBeEnabled({timeout: 20000});
  // Use the authenticated API to verify both publications without exposing secrets.
  await expect.poll(() => page.evaluate(async () => {
    const result = await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}});
    return (await result.json()).results.length;
  }), {timeout: 20000}).toBe(2);
  assert.equal(errors, 0);
  console.log(JSON.stringify({browser: browserName, studioCLI: true, bootstrap: true, automaticRun: true, reload: true, manualRun: true, errors}));
} catch {
  console.error(`Studio smoke failed during ${stage}.`);
  console.error(diagnostic.replace(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g, '[bootstrap link redacted]')); process.exitCode = 1;
} finally {
  clearTimeout(watchdog);
  child.kill('SIGINT');
  const force = setTimeout(() => child.kill('SIGKILL'), 5000);
  await Promise.allSettled([exited, browser?.close()]);
  clearTimeout(force);
  await rm(root, {recursive: true, force: true});
}
