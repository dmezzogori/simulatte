import {test, expect, type Page} from '@playwright/test';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

const fixtures = resolve('../prototypes/native_studio_observation/fixtures');
const secret = 'native-observation-browser-test';
const digest = (data: Buffer) => createHash('sha256').update(data).digest('hex');
const attempt = 'a'.repeat(32), key = {attempt_id: attempt, request_id: 'b'.repeat(64), generation: 1};
const revision = {number: 1, digest: 'd'.repeat(64)};
const prefix = (data: Buffer, generation: number) => ({attempt, generation, length: data.length, sha256: digest(data), url: `/api/prefix/${attempt}/${generation}`});

async function studio(page: Page, live: boolean) {
  const partial = readFileSync(resolve(fixtures, 'live-prefix.simtrace'));
  const completed = readFileSync(resolve(fixtures, 'live-completed.simtrace'));
  const result = {key, revision: 1, created: 0, trace: prefix(completed, 2)};
  const state: Record<string, unknown> = {revision, inputs: null, startup: {phase: 'ready', error: null},
    active: live ? {key, revision: 1, status: 'running', details: {}, prefix: prefix(partial, 1)} : null,
    results: live ? [] : [result], metadata: null, view: null, last_good: null, fatal: null,
    latest_attempt: null, experiments: [], replays: [], workers: {local: 1, workers: [], slots: [], error: null}};
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(value => {sessionStorage.setItem('simulatte-mode', 'studio'); sessionStorage.setItem('simulatte-session', value);}, secret);
  let publish = () => {}, sequence = 0, revoked = false;
  await page.routeWebSocket(/\/ws$/, socket => {
    publish = () => socket.send(JSON.stringify({v: 1, type: 'state', event_seq: ++sequence, body: state}));
    socket.onMessage(raw => {
      const command = JSON.parse(String(raw));
      if (command.type === 'auth') {expect(command.secret).toBe(secret); publish();}
      else socket.send(JSON.stringify({v: 1, type: 'response', id: command.id,
        body: command.type === 'state.get' ? {ok: true, state} : {ok: true}}));
    });
  });
  await page.route(/\/api\/prefix\//, async route => {
    if (revoked) {await route.fulfill({status: 410}); return;}
    const request = route.request();
    expect(request.headers()['x-simulatte-session']).toBe(secret);
    const bytes = request.url().endsWith('/1') ? partial : completed;
    const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers()['range'] ?? '');
    const start = range ? Number(range[1]) : 0, end = range ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
    await route.fulfill({status: range ? 206 : 200, body: request.method() === 'HEAD' ? '' : bytes.subarray(start, end + 1),
      headers: {'content-type': 'application/octet-stream', 'content-length': String(request.method() === 'HEAD' ? bytes.length : end-start+1),
        etag: `"${digest(bytes)}"`, 'accept-ranges': 'bytes', ...(range ? {'content-range': `bytes ${start}-${end}/${bytes.length}`} : {})}});
  });
  return {errors, finish: (status: 'succeeded' | 'cancelled' | 'failed') => {
    state.active = null; state.results = status === 'succeeded' ? [result] : [];
    state.latest_attempt = {key, revision: 1, status}; revoked = status !== 'succeeded'; publish();
  }};
}

for (const retention of ['full', 'bounded']) {
  test(`packaged ${retention} native observation displays experimental samples and final scalars`, async ({page}) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    await page.locator('input[type=file]').setInputFiles(resolve(fixtures, `observation-${retention}.simtrace`));
    await expect(page.locator('header small').last()).toContainText('Native production · experimental · KPI snapshots');
    await expect(page.locator('header small').last()).toContainText('spacing increased to fit limit');
    await expect(page.getByRole('button', {name: 'Next event', exact: true})).toBeDisabled();
    await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
    await expect(page.locator('.tile strong')).toHaveCount(8);
    await page.locator('input[type=range]').fill('40');
    await expect(page.locator('.sample-source').first()).toContainText('Recorded · t = 40');
    await expect(page.locator('.tile').filter({hasText: 'shopfloor/utilization'}).locator('strong')).not.toHaveText('—');
    await expect(page.getByText('Final recorded scalars', {exact: true})).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test('native KPI recording does not offer unsupported full entity replay', async ({page}) => {
  const {errors} = await studio(page, false);
  await page.goto('/');
  await expect(page.locator('header small').last()).toContainText('Native production · experimental');
  await page.getByText('Compare', {exact: true}).click();
  await page.getByLabel('Side A recording').selectOption(attempt);
  await expect(page.getByText('This backend supports KPI observation only.', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Replay at full level'})).toHaveCount(0);
  expect(errors).toEqual([]);
});

for (const terminal of ['succeeded', 'cancelled', 'failed'] as const) {
  test(`native live prefix stays experimental and handles ${terminal}`, async ({page}) => {
    const {errors, finish} = await studio(page, true);
    await page.goto('/');
    await expect(page.locator('header small').last()).toContainText('live committed prefix');
    await expect(page.locator('header small').last()).toContainText('Native production · experimental');
    await page.getByRole('tab', {name: 'Recorded KPIs', exact: true}).click();
    await expect(page.locator('.tile strong')).toHaveCount(8);
    await expect(page.getByText('Final recorded scalars', {exact: true})).toHaveCount(0);
    finish(terminal);
    if (terminal === 'succeeded') {
      await expect(page.locator('header small').last()).toContainText('completed');
      await expect(page.getByText('Final recorded scalars', {exact: true})).toBeVisible();
    } else {
      await expect(page.locator('.studio-controls')).toContainText(terminal);
      await expect(page.locator('.tile strong')).toHaveCount(0);
      await expect(page.getByText('Final recorded scalars', {exact: true})).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  });
}
