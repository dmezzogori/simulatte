// Installed-wheel commit-to-render observation on one host clock. No product instrumentation.
import {chromium, expect} from '../studio/node_modules/@playwright/test/index.mjs';
import {spawn} from 'node:child_process';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import assert from 'node:assert/strict';
const [python, output] = process.argv.slice(2);
const root = await mkdtemp(join(tmpdir(), 'simulatte-latency-'));
const child = spawn(resolve(python), [resolve('benchmarks/studio_latency.py'), root], {cwd: root, stdio: ['ignore','pipe','pipe']});
const exited = new Promise(resolve => child.once('close', resolve));
let browser, pending = '', commits = [], stderr = '';
child.stderr.on('data', value => {stderr = (stderr + value).slice(-8192);});
try {
  const link = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Studio startup timeout')), 20000);
    child.once('error', () => {clearTimeout(timer); reject(new Error('Studio launch failed'));});
    child.stdout.on('data', value => {
      pending += value;
      for (;;) {
        const newline = pending.indexOf('\n'); if (newline < 0) break;
        const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
        if (line.startsWith('SP4_COMMIT:')) commits.push(JSON.parse(line.slice(11)));
        const found = line.match(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/);
        if (found) {clearTimeout(timer); resolve(found[0]);}
      }
    });
  });
  browser = await chromium.launch();
  const page = await browser.newPage(); const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    globalThis.__frames = []; let previous = '';
    function sample() {
      const output = document.querySelector('footer output')?.textContent ?? '';
      if (output !== previous && /^t = [\d.]+/.test(output)) {
        previous = output;
        const t = Number(output.match(/^t = ([\d.]+)/)[1]);
        requestAnimationFrame(() => globalThis.__frames.push({t, epoch_ms: Date.now()}));
      }
      requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  });
  await page.goto(link);
  await expect(page.locator('.run-title strong')).toContainText('Run · revision', {timeout: 20000});
  await page.getByRole('button', {name: 'Follow live', exact: true}).click();
  const following = Date.now();
  await expect.poll(() => page.evaluate(async () => (await (await fetch('/api/state', {headers: {'X-Simulatte-Session': sessionStorage.getItem('simulatte-session')}})).json()).results.length), {timeout: 30000}).toBe(1);
  await page.waitForTimeout(200);
  const frames = await page.evaluate(() => globalThis.__frames);
  const samples = commits.filter(x => x.epoch_ms >= following && x.cursor?.[0] > 0).map(commit => {
    const frame = frames.find(frame => frame.t >= commit.cursor[0] && frame.epoch_ms >= commit.epoch_ms);
    assert(frame, 'Committed cursor was never presented');
    return {cursor: commit.cursor, milliseconds: frame.epoch_ms - commit.epoch_ms};
  });
  assert(samples.length >= 3); assert.equal(errors.length, 0);
  assert(samples.every(x => x.milliseconds >= 0 && x.milliseconds <= 2000));
  const report = {browser: await browser.version(), samples, max_ms: Math.max(...samples.map(x=>x.milliseconds)), errors,
    method: 'Same host epoch clock: SegmentReceiver.receive return after durable commit to next animation frame after controller output update; Follow enabled; excludes initial bootstrap'};
  await writeFile(output, JSON.stringify(report, null, 2)+'\n'); console.log(report);
} catch (error) {
  console.error(String(error).replace(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g,'[bootstrap]'));
  console.error(stderr.replace(/http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g,'[bootstrap]'));
  process.exitCode = 1;
} finally {
  await browser?.close(); child.kill('SIGINT');
  await Promise.race([exited, new Promise(resolve=>setTimeout(resolve, 5000))]);
  if (child.exitCode === null) child.kill('SIGKILL'); await exited;
  await rm(root, {recursive:true,force:true});
}
