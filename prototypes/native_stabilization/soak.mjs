// Real Studio clients. Credentials are read from private files and never logged.
import {createRequire} from 'node:module';
import {readFile, mkdir, writeFile, appendFile} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = dirname(fileURLToPath(import.meta.url));
const {chromium, firefox, webkit, expect} = createRequire(resolve(root, '../../studio/package.json'))('@playwright/test');
const args = Object.fromEntries(process.argv.slice(2).reduce((a, value, i, all) => i % 2 ? a : [...a, [value.slice(2), all[i + 1]]], []));
const targets = JSON.parse(await readFile(args.targets, 'utf8'));
const seconds = Number(args.seconds ?? 900), engines = (args.engines ?? 'chromium,firefox,webkit').split(',');

async function session(target) {
  const access = JSON.parse(await readFile(target.access, 'utf8'));
  await mkdir(target.output, {recursive: true});
  const log = async row => appendFile(resolve(target.output, 'browser.jsonl'), JSON.stringify({at: Date.now(), ...row})+'\n');
  const errors = [], clients = [];
  const scrub = value => String(value).replaceAll(access.secret, '[session]').replace(/bootstrap\?token=[^\s"']+/g, 'bootstrap?[redacted]');
  let storage, sessionKey;
  try {
  for (const engine of engines) {
    const server = await ({chromium, firefox, webkit}[engine]).launchServer({headless: true});
    const browser = await ({chromium, firefox, webkit}[engine]).connect(server.wsEndpoint());
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, ...(storage ? {storageState: storage} : {})});
    const client = {engine, server, browser, context, page: null, cdp: null};
    clients.push(client);
    if (!storage) {
      await context.addCookies([{name: access.cookie_name, value: access.cookie, url: access.origin, httpOnly: true, sameSite: 'Strict'}]);
      sessionKey = access.secret;
    }
    await context.addInitScript(key => {
      const Original = window.WebSocket;
      window.__soakSockets = [];
      window.WebSocket = class extends Original {
        constructor(...args) {super(...args); window.__soakSockets.push(this);}
      };
      if (key) {sessionStorage.setItem('simulatte-session', key); sessionStorage.setItem('simulatte-mode', 'studio');}
    }, sessionKey);
    const page = await context.newPage();
    page.on('pageerror', error => errors.push({engine, message: scrub(error.message)}));
    page.on('console', message => {if (message.type() === 'error' && !message.location().url.endsWith('/favicon.ico')) errors.push({engine, message: scrub(message.text())});});
    await page.goto(access.origin);
    await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 30000});
    if (!storage) {
      storage = await context.storageState();
      sessionKey = await page.evaluate(() => sessionStorage.getItem('simulatte-session'));
    }
    await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
    const cdp = engine === 'chromium' ? await context.newCDPSession(page) : null;
    Object.assign(client, {page, cdp});
  }
  const primary = clients[0].page;
  const state = () => primary.evaluate(async () => {
    const response = await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}});
    if (!response.ok) throw new Error('state HTTP '+response.status);
    return response.json();
  });
  const started = Date.now(), end = started + seconds * 1000;
  let lastAttempt = '', nextRun = 1, connected = 0, reloads = 0, seeks = 0, snapshots = 0;
  let lastSample = 0, lastAction = 0, lastReload = started, lastPrinted = 0, cancelled = new Set(), observed = new Map();
    while (Date.now() < end) {
      const current = await state();
      if (current.startup.phase === 'error' || current.fatal) throw new Error('Studio project became unusable');
      const active = current.active, latest = current.latest_attempt;
      if (active && active.key.attempt_id !== lastAttempt) {
        lastAttempt = active.key.attempt_id;
        await log({type: 'attempt', key: active.key, options: current.inputs?.options});
      }
      if (latest && ['succeeded', 'failed', 'cancelled', 'lost', 'validation_failed'].includes(latest.status) && !observed.has(latest.key.attempt_id)) {
        observed.set(latest.key.attempt_id, latest.status);
        await log({type: 'terminal', key: latest.key, status: latest.status, results: current.results.length});
      }
      if (!active && current.startup.phase === 'ready' && Date.now() < end - 15000) {
        if (!await primary.locator('.studio-parameters').evaluate(node => node.open))
          await primary.locator('.studio-parameters summary').click();
        const index = nextRun++;
        const fields = {case: ['flow', 'mixed', 'burst'][index % 3], retention: index % 2 ? 'full' : 'bounded',
          policy: Math.floor(index / 3) % 2 ? 'spt' : 'fifo', budget: [257, 1000, 4096][index % 3]};
        for (const [key, value] of Object.entries(fields)) await primary.getByLabel(new RegExp('^'+key+' ')).fill(String(value));
        await primary.getByLabel(/^fault /).selectOption(index % 9 === 8 ? 'true' : 'false');
        await primary.getByLabel('Seed', {exact: true}).fill(String(index % 3));
        await primary.getByRole('button', {name: 'Apply and run', exact: true}).click();
        await expect.poll(async () => (await state()).latest_attempt?.key.attempt_id, {timeout: 15000}).not.toBe(latest?.key.attempt_id);
        await log({type: 'apply', index, ...fields, fault: index % 9 === 8});
      }
      if (active?.prefix && current.inputs?.options?.parameters?.fault !== true && nextRun % 7 === 6 && !cancelled.has(active.key.attempt_id)) {
        const t = Number(current.progress?.body?.simulation_time ?? 0);
        if (t > 500) {
          const old = active.prefix;
          await primary.getByRole('button', {name: 'Cancel run', exact: true}).click();
          cancelled.add(active.key.attempt_id);
          await expect.poll(async () => (await state()).latest_attempt?.status).toBe('cancelled');
          const response = await clients[0].context.request.get(new URL(old.url, access.origin).href, {headers: {'X-Simulatte-Session': access.secret}});
          const status = response.status();
          if (status !== 410) throw new Error('cancelled prefix was not revoked');
          await log({type: 'cancel', key: active.key, prefix_http: status, native_time: t});
        }
      }
      if (Date.now() - lastAction > 30000 && active) {
        const client = clients[Math.floor(connected / 2) % clients.length];
        if (connected % 2 === 0) {
          await client.page.evaluate(() => window.__soakSockets.filter(socket => socket.readyState === WebSocket.OPEN).forEach(socket => socket.close(4000, 'stabilization reconnect')));
          await expect(client.page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 15000});
          connected++;
          await log({type: 'reconnect', engine: client.engine, key: active.key});
        } else {
          const range = client.page.locator('input[type=range]');
          const maximum = Number(await range.getAttribute('max'));
          if (maximum > 0 && !await range.isDisabled()) {
            await range.fill(String(Math.floor(maximum / 2)));
            await client.page.getByRole('button', {name: 'Follow live', exact: true}).click();
            seeks++;
          }
          connected++;
          await log({type: 'seek-follow', engine: client.engine, key: active.key});
        }
        lastAction = Date.now();
      }
      if (Date.now() - lastReload > 90000) {
        const client = clients[reloads % clients.length];
        await client.page.reload();
        await expect(client.page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 15000});
        await client.page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
        reloads++; lastReload = Date.now();
        await log({type: 'reload', engine: client.engine, active: active?.key ?? null});
      }
      if (Date.now() - lastSample > 15000) {
        for (const client of clients) {
          let heap = null;
          if (client.cdp) {
            const before = await client.cdp.send('Runtime.getHeapUsage');
            await client.cdp.send('HeapProfiler.collectGarbage');
            const after = await client.cdp.send('Runtime.getHeapUsage');
            heap = {before: before.usedSize, after: after.usedSize, total: after.totalSize};
          }
          const view = await client.page.evaluate(() => ({tiles: document.querySelectorAll('.tile strong').length,
            canvases: document.querySelectorAll('canvas').length, nodes: document.querySelectorAll('*').length,
            info: [...document.querySelectorAll('header small')].at(-1)?.textContent ?? '',
            progress: document.querySelector('.studio-controls output')?.textContent ?? ''}));
          await log({type: 'sample', engine: client.engine, browser_pid: client.server.process().pid, heap, view,
            active: active?.key ?? null, parameters: current.inputs?.options?.parameters, results: current.results.length});
        }
        snapshots++; lastSample = Date.now();
      }
      if (Date.now() - lastPrinted > 30000) {
        console.log(JSON.stringify({host: target.name, elapsed: Math.round((Date.now()-started)/1000), terminal: observed.size,
          active: active?.status ?? null, reloads, reconnect_actions: connected, samples: snapshots, errors: errors.length}));
        lastPrinted = Date.now();
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    const current = await state();
    await writeFile(resolve(target.output, 'browser-summary.json'), JSON.stringify({passed: errors.length === 0, seconds: (Date.now()-started)/1000,
      engines, observed: Object.fromEntries(observed), cancelled: [...cancelled], reloads, reconnect_actions: connected, seeks,
      snapshots, errors, latest: current.latest_attempt, results: current.results.length}, null, 2)+'\n');
    if (errors.length) throw new Error('browser errors: '+JSON.stringify(errors));
  } finally {
    for (const client of clients) {await client.browser.close(); await client.server.close();}
  }
}

const results = await Promise.allSettled(targets.map(session));
for (let i = 0; i < results.length; i++) {
  if (results[i].status === 'rejected') console.error(JSON.stringify({host: targets[i].name, failed: true, error: String(results[i].reason).replace(/bootstrap\?token=[^\s"']+/g, 'bootstrap?[redacted]')}));
}
if (results.some(result => result.status === 'rejected')) process.exitCode = 1;
