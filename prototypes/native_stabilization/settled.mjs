// Verify all eight charts settle on a real committed result after the timed run.
import {createRequire} from 'node:module';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = dirname(fileURLToPath(import.meta.url));
const {chromium, firefox, webkit, expect} = createRequire(resolve(root, '../../studio/package.json'))('@playwright/test');
const targets = JSON.parse(await readFile(process.argv[2], 'utf8'));
await Promise.all(targets.map(async target => {
  const access = JSON.parse(await readFile(target.access, 'utf8'));
  const output = resolve(target.output, '../settled-'+target.name);
  await mkdir(output, {recursive: true});
  const rows = [];
  for (const [engine, type] of Object.entries({chromium, firefox, webkit})) {
    const browser = await type.launch();
    try {
      const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
      await context.addCookies([{name: access.cookie_name, value: access.cookie, url: access.origin, httpOnly: true, sameSite: 'Strict'}]);
      await context.addInitScript(key => {
        sessionStorage.setItem('simulatte-session', key); sessionStorage.setItem('simulatte-mode', 'studio');
      }, access.secret);
      const page = await context.newPage(), errors = [];
      const scrub = value => String(value).replaceAll(access.secret, '[session]');
      page.on('pageerror', error => errors.push({kind: 'pageerror', message: scrub(error.message)}));
      page.on('console', message => {
        if (message.type() === 'error' && !message.location().url.endsWith('/favicon.ico'))
          errors.push({kind: 'console', message: scrub(message.text()), location: scrub(message.location().url)});
      });
      const started = Date.now();
      await page.goto(access.origin);
      await expect(page.locator('.studio-controls > div [role=status]')).toHaveText('connected', {timeout: 30000});
      await expect(page.locator('header small').last()).toContainText('completed', {timeout: 30000});
      const metadata = Date.now();
      await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
      await expect(page.locator('.tile strong')).toHaveCount(8, {timeout: 45000});
      await expect(page.locator('.chart .uplot')).toHaveCount(8, {timeout: 45000});
      const ready = Date.now();
      const view = await page.evaluate(() => ({info: [...document.querySelectorAll('header small')].at(-1)?.textContent,
        tiles: document.querySelectorAll('.tile strong').length, charts: document.querySelectorAll('.chart .uplot').length,
        alerts: [...document.querySelectorAll('[role=alert]')].map(node => node.textContent?.trim()).filter(Boolean)}));
      if (engine === 'chromium') await page.screenshot({path: resolve(output, 'studio.png')});
      rows.push({engine, metadata_ms: metadata-started, charts_after_metadata_ms: ready-metadata, view, errors});
    } finally {await browser.close();}
  }
  await writeFile(resolve(output, 'settled.json'), JSON.stringify({passed: rows.every(row => !row.errors.length && !row.view.alerts.length), rows}, null, 2)+'\n');
  console.log(JSON.stringify({host: target.name, rows}));
}));
