import {expect, it, vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {CompareSideView, ReplayNotifications, compareEntries, type CompareEntry, type SideState} from '../src/compare-panel';
import type {ExperimentReport, Prefix, ReplayEntry, StudioState} from '../src/studio-client';

const trace = (attempt: string): Prefix => ({attempt, generation: 1, length: 10, sha256: 'f'.repeat(64), url: `/api/prefix/${attempt}/1`});
const key = (id: string) => ({request_id: id.repeat(64), attempt_id: id.repeat(32), generation: 1});
const replay = (id: string, verified: boolean, reason: string | null = null): ReplayEntry => ({request_id: id.repeat(64), original: 'c'.repeat(64),
  status: 'succeeded', verified, reason, key: key(id), trace: trace(id.repeat(32))});
const state = (replays: ReplayEntry[]): Pick<StudioState, 'results' | 'replays'> => ({
  results: [{key: key('a'), revision: 3, created: 0, trace: trace('a'.repeat(32))}, {key: key('b'), revision: 4, created: 0, trace: null}],
  replays: [...replays, {...replay('f', true), status: 'running', trace: null}],
});
const report = {run_id: 'r'.repeat(64), name: 'sweep', configurations: [{index: 0, label: 'n=3/base', parameters: {}, counts: {}, mixed_runtime: false}],
  replications: [
    {configuration: 0, index: 0, status: 'succeeded', attempt: 'c'.repeat(32), request_id: 'c'.repeat(64), trace: trace('c'.repeat(32))},
    {configuration: 0, index: 1, status: 'failed', attempt: null, request_id: null, trace: null},
  ]} as unknown as ExperimentReport;
const render = (side: SideState, connected = true) => renderToStaticMarkup(
  <CompareSideView index={0} side={side} connected={connected} onReplay={() => {}} host={{current: null}} time={{current: null}} tiles={{current: null}} />);

it('lists committed results, replications and verified replays; unverified replays need the opt-in', () => {
  const listed = compareEntries(state([replay('d', true), replay('e', false, 'digest')]), report, false);
  expect(listed.entries.map(entry => [entry.kind, entry.label, entry.request_id])).toEqual([
    ['result', 'Run · revision 3 · aaaaaaaa', 'a'.repeat(64)],
    ['replication', 'sweep · n=3/base · replication 0', 'c'.repeat(64)],
    ['replay', 'Replay of sweep · n=3/base · replication 0', 'd'.repeat(64)],
  ]);
  expect(listed.hidden).toBe(1);
  const opted = compareEntries(state([replay('d', true), replay('e', false, 'digest')]), report, true);
  expect(opted.hidden).toBe(0);
  expect(opted.entries.find(entry => entry.request_id === 'e'.repeat(64))).toMatchObject({kind: 'replay', verified: false, reason: 'digest'});
});

it('shows the replay action and no scene for a KPI-only result', () => {
  const entry = compareEntries(state([]), report, false).entries[1]!;
  const html = render({phase: 'refused', entry, reason: {code: 'kpi_only', level: 'kpi'}});
  expect(html).toContain('KPI-only recording');
  expect(html).toContain('>Replay at full level</button>');
  expect(html).toMatch(/<div class="compare-scene"[^>]*hidden=""/);
  expect(render({phase: 'refused', entry, reason: {code: 'kpi_only', level: 'kpi'}}, false)).toMatch(/<button disabled="">Replay at full level/);
  const unknown: CompareEntry = {...entry, request_id: null};
  expect(render({phase: 'refused', entry: unknown, reason: {code: 'kpi_only', level: 'kpi'}})).toMatch(/<button disabled="">Replay at full level/);
  // A loaded full recording has a visible scene host and no replay action.
  const ready = render({phase: 'ready', entry, level: 'full'});
  expect(ready).not.toContain('Replay at full level');
  expect(ready).not.toMatch(/<div class="compare-scene"[^>]*hidden=""/);
});

it('labels an unverified replay with its reason and explains the exclusion', () => {
  const entry = compareEntries(state([replay('e', false, 'digest differs')]), report, true).entries.at(-1)!;
  const html = render({phase: 'ready', entry, level: 'full'});
  expect(html).toContain('unverified replay: digest differs');
  const verified = compareEntries(state([replay('d', true)]), report, false).entries.at(-1)!;
  expect(render({phase: 'ready', entry: verified, level: 'full'})).toContain('>verified replay<');
  const waiting = render({phase: 'replaying', entry, replay: 'e'.repeat(64), status: 'succeeded', verified: false, reason: 'digest differs'});
  expect(waiting).toContain('unverified replay: digest differs');
  expect(waiting).toContain('Include unverified replays');
  expect(render({phase: 'replaying', entry, replay: 'e'.repeat(64), status: 'running', verified: null, reason: null})).toContain('Replay running');
});

it('routes replay_state notifications to subscribers', () => {
  const replays = new ReplayNotifications();
  const seen = vi.fn();
  const stop = replays.subscribe(seen);
  replays.notification({v: 1, type: 'progress', event_seq: 1, body: {}});
  replays.notification({v: 1, type: 'replay_state', event_seq: 2, body: {request_id: 'e'.repeat(64), original: 'c'.repeat(64), status: 'running', verified: null}});
  replays.notification({v: 1, type: 'replay_state', event_seq: 3, body: 'invalid'});
  expect(seen).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({status: 'running'}));
  stop();
  replays.notification({v: 1, type: 'replay_state', event_seq: 4, body: {request_id: 'e'.repeat(64), original: 'c'.repeat(64), status: 'failed', verified: null}});
  expect(seen).toHaveBeenCalledTimes(1);
});
