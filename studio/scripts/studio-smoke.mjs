import {chromium, firefox, webkit, expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, rm, writeFile, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

// Executable may be wheel-installed. All model files and process state are isolated.
// Never forward captured CLI output or navigation errors containing bootstrap URLs.
const [executable, browserName = 'chromium', mode = 'defaults'] = process.argv.slice(2);
const required = mode === 'required';
const engine = {chromium, firefox, webkit}[browserName];
if (!engine) throw new Error('Unknown browser');
if (!executable) throw new Error('Usage: node studio-smoke.mjs <simulatte-executable> [chromium|firefox|webkit]');
const root = await mkdtemp(join(tmpdir(), 'simulatte-live-smoke-'));
await writeFile(join(root, 'pyproject.toml'), '[tool.simulatte.bundle]\ninclude = ["plant.py"]\n');
const model = `import time
from dataclasses import dataclass
from simulatte import model
from simulatte.server import Server
from simulatte.layout import Point
@dataclass(frozen=True)
class Params:
    ${required ? 'label: str' : 'label: str = \"default\"'}
    delay: float = 0.05
@model(params=Params, horizon=4)
def build(env, layout, params):
    machine = Server(env=env, capacity=1, name='machine')
    layout.place(machine, position=Point(1, 2))
    def work():
        for _ in range(4):
            time.sleep(params.delay)
            yield env.timeout(1)
    env.process(work())
`;
await writeFile(join(root, 'plant.py'), model);
const child = spawn(resolve(executable), ['studio', join(root, 'plant.py'), '--no-browser', '--port', '0'], {
  cwd: root, env: {...process.env, PYTHONUNBUFFERED: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
});
const exited = new Promise(done => child.once('close', done));
let diagnostic = "";
child.stderr.on("data", data => {diagnostic = (diagnostic + data.toString()).slice(-8192);});
let browser;
let page;
let previewOutcome;
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
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  let errors = 0;
  page.on('pageerror', error => {errors++; console.error('Page error: ' + (error.stack ?? error.message));});
  page.on('response', async response => {
    if (new URL(response.url()).pathname !== '/api/layout/validate') return;
    try {const body = await response.json(); previewOutcome = {status: response.status(), ok: body.ok, requires_rerun: body.requires_rerun, error: body.error};} catch {}
  });
  stage = 'bootstrap';
  await page.goto(link);
  await expect(page.getByRole('region', {name: 'Studio execution'}).or(page.locator('.studio-controls'))).toBeVisible();
  await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 20000});
  if (required) {
    stage = 'required parameter startup';
    await page.getByText('Parameters', {exact: true}).click();
    await expect(page.getByRole('button', {name: 'Run', exact: true})).toBeDisabled();
    await expect(page.getByRole('button', {name: 'Apply and run'})).toBeDisabled();
    await page.getByRole('button', {name: 'Use empty string for label'}).click();
    await page.getByRole('button', {name: 'Apply and run'}).click();
    await page.getByText('Parameters', {exact: true}).click();
  }
  stage = 'automatic execution';
  await expect(page.locator('.run-title strong')).toContainText('Run · revision', {timeout: 20000});
  const before = await page.locator('.run-title strong').textContent();
  stage = 'reload';
  await page.reload();
  await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 20000});
  await expect(page.locator('.run-title strong')).toHaveText(before, {timeout: 20000});
  // The title now appears at the first live prefix, before durable publication.
  await expect.poll(() => page.evaluate(async () => (await (await fetch('/api/state', {
    headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')},
  })).json()).results.length), {timeout: 20000}).toBe(1);
  stage = 'manual execution';
  await page.getByRole('button', {name: 'Run', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Run', exact: true})).toBeEnabled({timeout: 20000});
  // Use the authenticated API to verify both publications without exposing secrets.
  await expect.poll(() => page.evaluate(async () => {
    const result = await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}});
    return (await result.json()).results.length;
  }), {timeout: 20000}).toBe(2);
  stage = 'parameter drafts';
  await page.getByText('Parameters', {exact: true}).click();
  const delay = page.getByLabel('delay', {exact: false});
  await expect(delay).toHaveValue('0.05');
  await delay.fill('NaN');
  await expect(page.getByRole('button', {name: 'Apply and run'})).toBeDisabled();
  await delay.fill('0.01');
  const revision = await page.evaluate(async () => (await (await fetch('/api/state', {
    headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')},
  })).json()).revision.number);
  await page.getByRole('button', {name: 'Apply and run'}).click();
  await expect.poll(() => page.evaluate(async () => {
    const state = await (await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}})).json();
    return {revision: state.revision.number, results: state.results.length, delay: state.inputs.options.parameters.delay};
  }), {timeout: 20000}).toEqual({revision: revision + 1, results: 3, delay: 0.01});
  await expect(delay).toHaveValue('0.01');
  stage = 'preserved parameter conflict';
  await delay.fill('0.02');
  // A second mutation emulates another authenticated tab; no credentials leave the page.
  await page.evaluate(async () => {
    const ws = new WebSocket(location.origin.replace('http:', 'ws:') + '/ws');
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Second tab timeout')), 10000);
      ws.onopen = () => ws.send(JSON.stringify({v: 1, type: 'auth', secret: sessionStorage.getItem('simulatte-session')}));
      ws.onmessage = event => {
        const message = JSON.parse(event.data);
        if (message.type === 'state') ws.send(JSON.stringify({v: 1, type: 'params.set', id: 'second-tab',
          revision: message.body.revision, body: {parameters: {...message.body.inputs.options.parameters, delay: 0.03}}}));
        if (message.type === 'response') {clearTimeout(timer); ws.close(); message.body.ok ? resolve() : reject(new Error('Mutation failed'));}
      };
    });
  });
  await expect(page.getByRole('button', {name: 'Keep edits on new revision'})).toBeVisible({timeout: 20000});
  await expect(delay).toHaveValue('0.02');
  await expect(page.getByRole('button', {name: 'Apply and run'})).toBeDisabled();
  await page.getByRole('button', {name: 'Keep edits on new revision'}).click();
  await expect(delay).toHaveValue('0.02');
  await expect(page.getByRole('button', {name: 'Apply and run'})).toBeEnabled();
  stage = 'structured layout editing';
  await page.getByText('Layout', {exact: true}).click();
  await page.getByRole('button', {name: 'Set grid', exact: true}).click();
  await page.getByRole('button', {name: 'Set dx', exact: true}).click();
  await page.getByLabel('layout.grid.dx', {exact: true}).fill('2');
  await page.getByRole('button', {name: 'Validate layout', exact: true}).click();
  await expect(page.getByText('Physical preparation passed in a disposable process; saving starts a fresh run.', {exact: true})).toBeVisible({timeout: 20000});
  await page.getByRole('button', {name: 'Apply layout', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Undo layout', exact: true})).toBeEnabled({timeout: 20000});
  await page.getByRole('button', {name: 'Undo layout', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Set grid', exact: true})).toBeVisible({timeout: 20000});
  await expect(page.getByRole('button', {name: 'Redo layout', exact: true})).toBeEnabled({timeout: 20000});
  await page.getByRole('button', {name: 'Redo layout', exact: true}).click();
  await expect(page.getByLabel('layout.grid.dx', {exact: true})).toHaveValue('2', {timeout: 20000});
  stage = 'canvas keyboard movement';
  await expect(page.getByLabel('Selected handles')).toContainText('machine');
  await page.getByLabel('Selected handles').selectOption({label: 'machine'});
  await page.getByLabel('Move x', {exact: true}).fill('2');
  await page.getByRole('button', {name: 'Move selected', exact: true}).click();
  await expect(page.getByLabel('layout.placements.machine.position[0]', {exact: true})).toHaveValue('3');
  await page.getByRole('button', {name: 'Apply layout', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Undo layout', exact: true})).toBeEnabled({timeout: 20000});
  stage = 'canvas drag gesture';
  await expect(page.getByRole('button', {name: 'Move selected', exact: true})).toBeEnabled({timeout: 20000});
  await page.getByRole('button', {name: 'Fit layout', exact: true}).click();
  const canvas = page.locator('.layout-canvas canvas');
  await canvas.scrollIntoViewIfNeeded();
  const bounds = await canvas.boundingBox();
  assert.ok(bounds);
  await expect.poll(() => page.evaluate(({x, y}) => document.elementFromPoint(x, y)?.tagName,
    {x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2})).toBe('CANVAS');
  await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await expect(page.getByLabel('layout.placements.machine.position[0]', {exact: true})).toHaveValue('3');
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 40, bounds.y + bounds.height / 2 - 20, {steps: 8});
  await expect(page.getByLabel('layout.placements.machine.position[0]', {exact: true})).toHaveValue('3');
  await page.mouse.up();
  await expect(page.getByLabel('layout.placements.machine.position[0]', {exact: true})).toHaveValue('4');
  stage = 'lane vertex editing';
  await page.getByRole('button', {name: 'Set network', exact: true}).click();
  await page.getByLabel('layout.network variant', {exact: true}).selectOption({label: 'lanes'});
  const network = page.getByRole('group', {name: 'layout.network', exact: true});
  await network.getByRole('button', {name: 'Set lanes', exact: true}).click();
  await page.getByLabel('New layout.network.lanes name', {exact: true}).fill('main');
  await network.getByRole('button', {name: 'Add named entry', exact: true}).click();
  await page.getByRole('button', {name: 'Set vertices', exact: true}).click();
  const vertex = index => `layout.network.lanes.main.vertices[${index}]`;
  await page.getByLabel(vertex(0) + '.id', {exact: true}).fill('a');
  await page.getByLabel(vertex(0) + '.id', {exact: true}).press('Tab');
  await page.getByLabel(vertex(1) + '.id', {exact: true}).fill('b');
  await page.getByLabel(vertex(1) + '.id', {exact: true}).press('Tab');
  await page.getByLabel(vertex(1) + '.position[0]', {exact: true}).fill('4');
  await page.getByRole('button', {name: 'Insert after 1', exact: true}).click();
  await expect(page.getByLabel(vertex(2) + '.id', {exact: true})).toHaveValue('b');
  await page.getByRole('button', {name: 'Remove item 2', exact: true}).click();
  await expect(page.getByLabel(vertex(0) + '.id', {exact: true})).toHaveValue('a');
  await expect(page.getByLabel(vertex(1) + '.id', {exact: true})).toHaveValue('b');
  await page.getByRole('button', {name: 'Validate layout', exact: true}).click();
  await expect(page.getByText('Physical preparation passed in a disposable process; saving starts a fresh run.', {exact: true})).toBeVisible({timeout: 20000});
  stage = 'presentation-only playback and pinned HTML';
  await expect.poll(() => page.evaluate(async () => {
    const state = await (await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}})).json();
    return state.active === null && state.results.length > 0;
  }), {timeout: 20000}).toBe(true);
  await page.getByText('Parameters', {exact: true}).click();
  await page.getByText('Layout', {exact: true}).click();
  await expect(page.getByRole('button', {name: 'Export video / PNG', exact: true})).toBeEnabled();
  await page.getByRole('button', {name: 'Export video / PNG', exact: true}).click();
  const overlay = JSON.parse(await readFile(join(root, 'layout.json'), 'utf8'));
  overlay.placements.machine.visual = {label: 'presentation-only-label'};
  const presentation = await page.evaluate(async layer => {
    const headers = {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')};
    const before = await (await fetch('/api/state', {headers})).json();
    const response = await fetch('/api/layout', {method: 'PUT', headers: {...headers,
      'X-Simulatte-Revision': JSON.stringify(before.revision), 'X-Simulatte-Command': crypto.randomUUID(), 'Content-Type': 'application/json'},
      body: JSON.stringify(layer)});
    const outcome = await response.json();
    if (!outcome.ok) throw new Error('Presentation save failed');
    return {count: before.results.length, attempt: before.results[0].key.attempt_id, revision: outcome.revision.number};
  }, overlay);
  await expect(page.getByRole('dialog', {name: 'Export recording'})).toBeVisible();
  await page.getByRole('button', {name: 'Close', exact: true}).click();
  await expect.poll(() => page.evaluate(async () => {
    const state = await (await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}})).json();
    return {count: state.results.length, attempt: state.view.key.attempt_id, revision: state.revision.number};
  })).toEqual(presentation);
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Export HTML', exact: true}).click();
  const downloaded = await waiting;
  const htmlPath = join(root, 'presentation.html'); await downloaded.saveAs(htmlPath);
  const html = await readFile(htmlPath, 'utf8');
  const embedded = JSON.parse(html.match(/<script id="presentation" type="application\/json">(.*?)<\/script>/s)[1]);
  assert.equal(embedded.layout.resolved.placements.machine.visual.label, 'presentation-only-label');
  const offlineContext = await browser.newContext({offline: true});
  const offlinePage = await offlineContext.newPage();
  offlinePage.on('pageerror', () => errors++);
  const offlineRequests = []; offlinePage.on('request', request => {if (!request.url().startsWith('file:')) offlineRequests.push(request.url());});
  await offlinePage.goto(pathToFileURL(htmlPath).href);
  await expect(offlinePage.locator('output')).toContainText('t = 0');
  assert.deepEqual(offlineRequests, []); await offlineContext.close();
  await page.getByText('Parameters', {exact: true}).click();
  stage = 'source schema conflict';
  await writeFile(join(root, 'plant.py'), model.replace('delay: float = 0.05', 'delay: int = 1'));
  await expect(page.getByRole('button', {name: 'Keep edits on new revision'})).toBeVisible({timeout: 20000});
  // Wait for inspection, rather than accepting the intermediate invalidation revision.
  await expect.poll(() => page.evaluate(async () => {
    const headers = {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')};
    const state = await (await fetch('/api/state', {headers})).json();
    const metadata = await (await fetch(state.metadata.url, {headers})).json();
    return metadata.models.find(model => model.name === metadata.selected).parameters.fields.find(field => field.name === 'delay').type;
  }), {timeout: 20000}).toBe('int');
  await expect(page.getByText(/New schema:.*delay \(int\)/)).toBeVisible({timeout: 20000});
  await page.getByRole('button', {name: 'Keep edits on new revision'}).click();
  await expect(delay).toHaveValue('0.02');
  await expect(page.getByRole('button', {name: 'Apply and run'})).toBeDisabled();
  assert.equal(errors, 0);
  console.log(JSON.stringify({browser: browserName, studioCLI: true, bootstrap: true, automaticRun: !required, requiredParameterReview: required, reload: true, manualRun: true, parameterApply: true, parameterConflict: true, layoutEditing: true, layoutUndoRedo: true, canvasKeyboardMove: true, canvasDrag: true, sourceSchemaConflict: true, physicalPreview: true, stableLaneVertices: true, presentationWithoutRun: true, pinnedExportDialog: true, presentationOfflineHtml: true, errors}));
} catch (error) {
  console.error(`Studio smoke failed during ${stage}.`);
  if (previewOutcome) console.error(JSON.stringify(previewOutcome));
  if (page) console.error(JSON.stringify({body: (await page.locator('body').innerText().catch(() => '')).slice(0, 4096), alerts: await page.locator('[role=alert]').allTextContents().catch(() => []),
    editorStatus: await page.locator('.studio-layout [role=status]').allTextContents().catch(() => [])}));
  console.error(String(error).replace(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g, '[bootstrap link redacted]'));
  console.error(diagnostic.replace(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g, '[bootstrap link redacted]')); process.exitCode = 1;
} finally {
  clearTimeout(watchdog);
  child.kill('SIGINT');
  const force = setTimeout(() => child.kill('SIGKILL'), 5000);
  await Promise.allSettled([exited, browser?.close()]);
  clearTimeout(force);
  await rm(root, {recursive: true, force: true});
}
