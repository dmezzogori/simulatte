// Paired installed viewer openings of the same trace. Measure actual body reads.
import {createRequire} from 'node:module';
import {readFileSync, writeFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = dirname(fileURLToPath(import.meta.url));
const {chromium, firefox, webkit, expect} = createRequire(resolve(root, '../../studio/package.json'))('@playwright/test');
const targets = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const rows = [];

async function measure(browser, file) {
  const access = JSON.parse(readFileSync(file, 'utf8'));
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  try {
    await context.addCookies([{name: access.cookie_name, value: access.cookie, url: access.origin, httpOnly: true, sameSite: 'Strict'}]);
    await context.addInitScript(key => {
      sessionStorage.setItem('simulatte-session', key);
      sessionStorage.setItem('simulatte-mode', 'view');
      const stats = window.__chartStudy = {started: 0, completed: 0, bytes: 0, bad: 0};
      const fetch = window.fetch.bind(window);
      window.fetch = async (url, init) => {
        if (url !== '/trace' || init?.method === 'HEAD') return fetch(url, init);
        stats.started++;
        let response;
        try {response = await fetch(url, init);}
        catch (error) {stats.bad++; throw error;}
        const body = response.body;
        if (response.status !== 206 || !body) {stats.bad++; return response;}
        const expected = Number(response.headers.get('content-length'));
        const getReader = body.getReader.bind(body);
        body.getReader = (...args) => {
          const reader = getReader(...args), read = reader.read.bind(reader);
          let bytes = 0;
          reader.read = async () => {
            try {
              const chunk = await read();
              if (chunk.done) {
                stats.completed++;
                stats.bytes += bytes;
                if (bytes !== expected) stats.bad++;
              } else bytes += chunk.value.byteLength;
              return chunk;
            } catch (error) {stats.bad++; throw error;}
          };
          return reader;
        };
        return response;
      };
    }, access.secret);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const start = performance.now();
    await page.goto(access.origin);
    await expect(page.locator('.status')).toHaveText('paused', {timeout: 45000});
    const readyMs = performance.now() - start;
    await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
    await expect(page.locator('.chart .uplot')).toHaveCount(8);
    await expect(page.locator('[role=alert].error')).toHaveCount(0);
    const stats = await page.evaluate(() => window.__chartStudy);
    if (!stats.completed || stats.started !== stats.completed || stats.bad || errors.length)
      throw new Error('Incomplete/failed range read or page error: '+JSON.stringify({stats, pageErrors: errors.length}));
    const series = await page.locator('.tile > .muted').allTextContents();
    if (series.length !== 8) throw new Error('Expected eight KPI tiles');
    return {...stats, ready_ms: readyMs, series};
  } finally {await context.close();}
}

const finished = await Promise.allSettled(targets.map(async target => {
  for (const [engine, type] of Object.entries({chromium, firefox, webkit})) {
    const browser = await type.launch();
    try {
      for (let pair = 0; pair < 3; pair++) {
        for (const phase of pair % 2 ? ['candidate', 'baseline'] : ['baseline', 'candidate']) {
          const row = {host: target.host, engine, pair, phase, ...await measure(browser, target[phase])};
          rows.push(row);
          console.log(JSON.stringify({host: row.host, engine, pair, phase, reads: row.completed, ms: Math.round(row.ready_ms)}));
        }
      }
    } finally {await browser.close();}
  }
}));
for (const result of finished) if (result.status === 'rejected') {
  console.error(String(result.reason)); process.exitCode = 1;
}
writeFileSync(process.argv[3], JSON.stringify({passed: finished.every(result => result.status === 'fulfilled'), rows}, null, 2)+'\n');
