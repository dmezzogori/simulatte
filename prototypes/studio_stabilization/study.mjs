// Real browser sessions. Only prefix paths, timings and outcomes enter the logs.
import {createRequire} from 'node:module';
import {appendFileSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = dirname(fileURLToPath(import.meta.url));
const {chromium, firefox, webkit, expect} = createRequire(resolve(root, '../../studio/package.json'))('@playwright/test');
const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, all) => i % 2 ? a : [...a, [v.slice(2), all[i + 1]]], []));
const targets = JSON.parse(readFileSync(args.targets, 'utf8'));
const engines = (args.engines ?? 'chromium,firefox,webkit').split(',');
const seconds = Number(args.seconds ?? 900);
const keptOpen = (args['keep-open'] ?? '').split(',').filter(Boolean);
if (!Number.isFinite(seconds) || seconds <= 0 || keptOpen.some(engine => !engines.includes(engine)))
  throw new Error('Expected positive seconds and keep-open engines from the client set');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const prefixPath = path => /^\/api\/prefix\/[0-9a-f]{32}\/\d+$/.test(path);
const logError = error => String(error).replace(/bootstrap\?token=[^\s"']+/g, 'bootstrap?[redacted]');

async function session(target) {
  const access = JSON.parse(readFileSync(target.access, 'utf8'));
  const scrub = value => logError(value).replaceAll(access.secret, '[session]').replaceAll(access.cookie, '[cookie]');
  mkdirSync(target.output, {recursive: true});
  const log = row => appendFileSync(resolve(target.output, 'browser.jsonl'), JSON.stringify({at: Date.now(), ...row})+'\n');
  const clients = [], errors = [];
  try {
    for (const engine of engines) {
      const browser = await ({chromium, firefox, webkit}[engine]).launch();
      const client = {engine, browser};
      clients.push(client);
      const context = await browser.newContext({viewport: {width: 1440, height: 1000},
        extraHTTPHeaders: {'X-Studio-Study-Client': args.probe === 'cancel' ? 'cancel-probe' : engine}});
      await context.addCookies([{name: access.cookie_name, value: access.cookie, url: access.origin, httpOnly: true, sameSite: 'Strict'}]);
      await context.addInitScript(key => {
        sessionStorage.setItem('simulatte-session', key);
        sessionStorage.setItem('simulatte-mode', 'studio');
        const Original = window.WebSocket;
        window.__studySocket = null;
        window.WebSocket = class extends Original {
          constructor(...args) {super(...args); window.__studySocket = this;}
        };
        const fetch = window.fetch.bind(window);
        const scopes = new WeakMap();
        const navigation = crypto.randomUUID();
        let id = 0, nextFetch = 0;
        window.fetch = async (input, options) => {
          if (typeof input !== 'string' || !input.startsWith('/api/prefix/')) return fetch(input, options);
          const signal = options?.signal;
          let scope = signal ? scopes.get(signal) : null;
          if (signal && !scope) {
            scope = navigation+'-'+(++id);
            scopes.set(signal, scope);
            const mark = () => void window.__studyFetch({type: 'signal-abort', scope}).catch(() => {});
            if (signal.aborted) mark();
            else signal.addEventListener('abort', mark, {once: true});
          }
          const tag = navigation+'-'+(++nextFetch);
          const headers = new Headers(options?.headers);
          headers.set('X-Studio-Study-Fetch', tag);
          if (scope) headers.set('X-Studio-Study-Signal', scope);
          const observe = row => void window.__studyFetch({type: 'fetch-outcome', tag, scope, ...row}).catch(() => {});
          let response;
          try {response = await fetch(input, {...options, headers});}
          catch (error) {
            observe({outcome: 'fetch-error', error: error.name, aborted: signal?.aborted ?? false});
            throw error;
          }
          const status = response.status;
          if (!response.ok || !response.body) {
            observe({outcome: 'headers', status});
          } else {
            const body = response.body, getReader = body.getReader.bind(body);
            body.getReader = (...args) => {
              const reader = getReader(...args), read = reader.read.bind(reader);
              let bytes = 0;
              reader.read = async (...args) => {
                try {
                  const chunk = await read(...args);
                  if (chunk.done) observe({outcome: 'complete', status, bytes});
                  else bytes += chunk.value.byteLength;
                  return chunk;
                } catch (error) {
                  observe({outcome: 'body-error', status, bytes, error: error.name, aborted: signal?.aborted ?? false});
                  throw error;
                }
              };
              return reader;
            };
          }
          return response;
        };
      }, access.secret);
      const page = await context.newPage();
      await page.exposeBinding('__studyFetch', (_source, row) => log({...row, engine}));
      Object.assign(client, {context, page, cdp: engine === 'chromium' ? await context.newCDPSession(page) : null});
      const reads = new WeakMap();
      let count = 0;
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (!prefixPath(path)) return;
        const read = {id: ++count, path, method: request.method(), range: request.headers()['range'] ?? null,
          client: request.headers()['x-studio-study-client'] ?? engine,
          scope: request.headers()['x-studio-study-signal'] ?? null,
          tag: request.headers()['x-studio-study-fetch'] ?? null, started: Date.now()};
        reads.set(request, read);
        log({type: 'read-start', engine, ...read});
      });
      page.on('response', response => {
        const read = reads.get(response.request());
        if (!read) return;
        Object.assign(read, {status: response.status(), server_id: Number(response.headers()['x-studio-study-read']) || null,
          length: Number(response.headers()['content-length']) || null});
        log({type: 'read-headers', engine, ...read});
      });
      for (const event of ['requestfinished', 'requestfailed']) page.on(event, request => {
        const read = reads.get(request);
        if (!read) return;
        log({type: event, engine, ...read, failure: scrub(request.failure()?.errorText ?? '') || null});
      });
      const issue = (kind, value, location, stack) => {
        const row = {type: 'error', kind, engine, message: scrub(value), path: location ? new URL(location, access.origin).pathname : null,
          stack: stack ? scrub(stack).slice(0, 8000) : null};
        errors.push(row);
        log(row);
      };
      page.on('pageerror', error => issue('page', error.message, null, error.stack));
      page.on('console', message => {
        if (message.type() === 'error' && !message.location().url.endsWith('/favicon.ico'))
          issue('console', message.text(), message.location().url);
      });
      await page.goto(access.origin);
      await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 30000});
      await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
    }
    const primary = clients[0].page;
    const reloadable = clients.filter(({engine}) => !keptOpen.includes(engine));
    const state = () => primary.evaluate(async () => {
      const response = await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}});
      if (!response.ok) throw new Error('Studio state HTTP '+response.status);
      return response.json();
    });
    const revoke = async prefix => {
      const response = await clients[0].context.request.get(new URL(prefix.url, access.origin).href,
        {headers: {'X-Simulatte-Session': access.secret}});
      if (response.status() !== 410) throw new Error('cancelled prefix is still accessible');
    };
    const cancel = async active => {
      log({type: 'cancel-start', key: active.key, prefix: active.prefix.url});
      await primary.getByRole('button', {name: 'Cancel run', exact: true}).click();
      await expect.poll(async () => (await state()).latest_attempt?.status).toBe('cancelled');
      await revoke(active.prefix);
      log({type: 'cancelled', key: active.key, prefix: active.prefix.url, http: 410});
    };
    if (args.probe === 'cancel') {
      await expect.poll(async () => (await state()).startup.phase, {timeout: 25000}).toBe('ready');
      if (!(await state()).active) await primary.getByRole('button', {name: 'Run', exact: true}).click();
      await expect.poll(async () => (await state()).active?.prefix?.generation ?? 0, {timeout: 25000}).toBeGreaterThan(1);
      const active = (await state()).active;
      const url = active.prefix.url, length = active.prefix.length;
      if (length > (1 << 20)) throw new Error('probe prefix exceeds browser read window');
      const headers = primary.waitForResponse(response => new URL(response.url()).pathname === url &&
        response.request().headers()['x-studio-study-probe'] === '1');
      await primary.evaluate(({url, length}) => {
        window.__studyRead = fetch(url, {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session'),
          'X-Studio-Study-Probe': '1', Range: 'bytes=0-'+(length-1)}}).then(async response => {
          const bytes = await response.arrayBuffer();
          const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
          return {status: response.status, length: bytes.byteLength, hash};
        }).catch(error => ({error: String(error)}));
      }, {url, length});
      const response = await headers;
      await cancel(active);
      const body = await primary.evaluate(() => window.__studyRead);
      const result = {mode: 'cancel-probe', server_id: Number(response.headers()['x-studio-study-read']),
        status: response.status(), expected: length, prefix: url, body, errors,
        intact: body.length === length && body.hash === active.prefix.sha256};
      writeFileSync(resolve(target.output, 'summary.json'), JSON.stringify(result, null, 2)+'\n');
      console.log(JSON.stringify({host: target.name, ...result}));
      return;
    }

    const started = Date.now(), startClock = performance.now();
    const elapsed = () => performance.now() - startClock;
    const observed = new Map(), cancelled = new Set();
    let next = 0, last = '', lastAction = started, lastReload = started, lastHeap = 0, lastPrint = 0;
    let reloads = 0, reconnects = 0, seeks = 0, action = 0;
    for (;;) {
      const current = await state(), active = current.active, latest = current.latest_attempt;
      if (current.startup.phase === 'error' || current.fatal) throw new Error('Studio startup/execution became unusable');
      if (active && active.key.attempt_id !== last) {
        last = active.key.attempt_id;
        log({type: 'attempt', key: active.key, options: current.inputs?.options});
      }
      if (latest && ['succeeded', 'failed', 'cancelled', 'lost', 'validation_failed'].includes(latest.status) && !observed.has(latest.key.attempt_id)) {
        observed.set(latest.key.attempt_id, latest.status);
        log({type: 'terminal', key: latest.key, status: latest.status, results: current.results.length});
      }
      if (elapsed() >= seconds*1000 && !active) break;
      if (elapsed() >= (seconds+45)*1000) throw new Error('Final Studio attempt did not drain');
      if (!active && current.startup.phase === 'ready' && elapsed() < (seconds-15)*1000) {
        if (!await primary.locator('.studio-parameters').evaluate(node => node.open))
          await primary.locator('.studio-parameters summary').click();
        const index = next++, fields = {case: ['flow', 'mixed', 'burst'][index % 3], retention: index % 2 ? 'full' : 'bounded',
          policy: Math.floor(index/3) % 2 ? 'spt' : 'fifo', budget: args.budget ?? [257,1000,4096][index % 3]};
        for (const [key, value] of Object.entries(fields)) await primary.getByLabel(new RegExp('^'+key+' ')).fill(String(value));
        await primary.getByLabel(/^fault /).selectOption(index % 9 === 8 ? 'true' : 'false');
        await primary.getByLabel('Seed', {exact: true}).fill(String(index % 3));
        await primary.getByRole('button', {name: 'Apply and run', exact: true}).click();
        await expect.poll(async () => (await state()).latest_attempt?.key.attempt_id).not.toBe(latest?.key.attempt_id);
        log({type: 'apply', index, ...fields, fault: index % 9 === 8});
      }
      if (active?.prefix && next % 7 === 6 && current.inputs?.options?.parameters?.fault !== true &&
          Number(current.progress?.body?.simulation_time ?? 0) > 500 && !cancelled.has(last)) {
        await cancel(active);
        cancelled.add(last);
      }
      if (active?.prefix && Date.now()-lastAction > 30000) {
        const {page, engine} = clients[Math.floor(action/2) % clients.length];
        if (action++ % 2 === 0) {
          const closed = await page.evaluate(() => {
            if (window.__studySocket?.readyState !== WebSocket.OPEN) return false;
            window.__studySocket.close(4000, 'study reconnect'); return true;
          });
          if (closed) {
            await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 15000});
            log({type: 'reconnect', engine, key: active.key});
            reconnects++;
          }
        } else {
          const range = page.getByLabel('Simulation time', {exact: true});
          const max = Number(await range.getAttribute('max'));
          if (max > 2 && !await range.isDisabled()) {
            await range.fill(String(Math.floor(max/2)));
            await page.getByRole('button', {name: 'Follow live', exact: true}).click();
            log({type: 'seek-follow', engine, key: active.key});
            seeks++;
          }
        }
        lastAction = Date.now();
      }
      if (reloadable.length && Date.now()-lastReload > 90000) {
        const {page, engine} = reloadable[reloads % reloadable.length];
        log({type: 'reload-start', engine, key: active?.key ?? null});
        await page.reload();
        await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 15000});
        await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
        reloads++; lastReload = Date.now();
        log({type: 'reload-end', engine});
      }
      const measure = Date.now()-lastHeap > 15000;
      for (const {page, engine, cdp} of clients) {
        let heap = null;
        if (measure && cdp) {
          await cdp.send('HeapProfiler.collectGarbage');
          heap = (await cdp.send('Runtime.getHeapUsage')).usedSize;
        }
        const view = await page.evaluate(() => ({tiles: document.querySelectorAll('.tile strong').length,
          charts: document.querySelectorAll('.chart .uplot').length, nodes: document.querySelectorAll('*').length,
          info: [...document.querySelectorAll('header small')].at(-1)?.textContent ?? '',
          alerts: [...document.querySelectorAll('[role=alert]')].map(node => node.textContent?.trim()).filter(Boolean)}));
        view.alerts = view.alerts.map(scrub);
        log({type: 'sample', engine, heap, view, active: active?.key ?? null, results: current.results.length});
      }
      if (measure) lastHeap = Date.now();
      if (Date.now()-lastPrint > 30000) {
        console.log(JSON.stringify({host: target.name, elapsed: Math.round(elapsed()/1000),
          terminal: observed.size, errors: errors.length, reloads, reconnects, seeks}));
        lastPrint = Date.now();
      }
      await sleep(Number(args['sample-ms'] ?? 1000));
    }
    const result = {seconds: elapsed()/1000, started_at: started, ended_at: Date.now(), engines,
      kept_open: keptOpen, observed: Object.fromEntries(observed), cancelled: [...cancelled],
      reloads, reconnects, seeks, results: (await state()).results.length, errors};
    writeFileSync(resolve(target.output, 'summary.json'), JSON.stringify(result, null, 2)+'\n');
  } catch (error) {
    throw new Error(scrub(error));
  } finally {
    for (const client of clients) {
      log({type: 'client-close', engine: client.engine});
      await client.browser.close();
    }
  }
}
const results = await Promise.allSettled(targets.map(session));
for (const result of results) if (result.status === 'rejected') {console.error(logError(result.reason)); process.exitCode = 1;}
