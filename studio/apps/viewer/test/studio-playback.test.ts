import {afterEach, expect, it, vi} from 'vitest';
import {HttpSource} from '@simulatte/trace';
import {StudioPlayback} from '../src/studio-playback';
import type {Controller} from '../src/controller';
import type {StudioClient, StudioState, View} from '../src/studio-client';
const key = (id: string) => ({attempt_id: id.repeat(32), request_id: 'f'.repeat(64), generation: id === 'a' ? 1 : 2});
const ref = (id: string) => ({id, url: '/api/ref/' + id, length: 10, sha256: id.repeat(64)});
const result = (id: string) => ({key: key(id), revision: 1, created: 0,
  trace: {attempt: key(id).attempt_id, generation: 1, length: 12, sha256: 'f'.repeat(64), url: '/trace/' + id}});
const view = (id: string): View => ({revision: {number: 1, digest: 'e'.repeat(64)}, key: key(id), committed: true, layout: ref(id)});
const state = (id: string): StudioState => ({revision: view(id).revision, inputs: null,
  startup: {phase: 'ready', error: null}, active: null, results: [result(id)], metadata: null,
  view: view(id), last_good: view(id), fatal: null});
const flush = async () => {for (let i = 0; i < 12; i++) await Promise.resolve();};
afterEach(() => vi.restoreAllMocks());
function setup() {
  const controller = {source: null, playback: null, presentation: null,
    open: vi.fn(async (source: unknown) => {controller.source = source as null;}),
    setPresentation: vi.fn(async () => {}), attachLive: vi.fn(async () => {})};
  const client = {state: null, reference: vi.fn(async () => ({schema_version: '1.0'}))};
  const errors = vi.fn();
  return {controller, client, errors, playback: new StudioPlayback(controller as unknown as Controller,
    client as unknown as StudioClient, {}, errors)};
}
it('fences a slow committed result when a newer result is selected', async () => {
  let resolve: (source: HttpSource) => void = () => {};
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => url.endsWith('a')
    ? new Promise(done => {resolve = done;}) : ({url} as HttpSource));
  const {controller, playback, errors} = setup();
  playback.update(state('a')); playback.update(state('b')); await flush();
  resolve({url: '/trace/a'} as HttpSource); await flush();
  expect(controller.open).toHaveBeenCalledTimes(1);
  expect(controller.open.mock.calls[0]![0]).toEqual({url: '/trace/b'});
  expect(errors).not.toHaveBeenCalled(); playback.dispose();
});
it('keeps a selected history view pinned through automatic state changes', async () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => ({url} as HttpSource));
  const {controller, playback} = setup();
  playback.select(result('a').trace, 1, view('a')); await flush();
  playback.update(state('b')); await flush();
  expect(controller.open).toHaveBeenCalledTimes(1);
  playback.dispose();
});
it('applies a presentation-only update without reopening the trace and drops late view reads', async () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => ({url} as HttpSource));
  const {controller, client, playback} = setup();
  playback.update(state('a')); await flush();
  let resolve: (value: {schema_version: string}) => void = () => {};
  client.reference.mockImplementationOnce(() => new Promise(done => {resolve = done;}));
  const changed = state('a'); changed.view = {...view('a'), layout: ref('c')};
  playback.update(changed); playback.detach();
  resolve({schema_version: '1.0'}); await flush();
  expect(controller.open).toHaveBeenCalledTimes(1);
  expect(controller.setPresentation).not.toHaveBeenCalled(); playback.dispose();
});

it('restores the recorded layout when selecting the already-open history entry', async () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => ({url} as HttpSource));
  const {controller, playback} = setup();
  playback.update(state('a')); await flush();
  playback.select(result('a').trace, 1, null); await flush();
  expect(controller.setPresentation).toHaveBeenCalledWith(null);
  expect(controller.open).toHaveBeenCalledTimes(1);
  playback.resume(); playback.update(state('a'));
  // Returning to live presentation must fetch and apply the overlay again.
  await flush();
  expect(controller.setPresentation).toHaveBeenLastCalledWith({schema_version: '1.0'});
  playback.dispose();
});
it('drops a pending presentation read when history selects the recorded layout', async () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => ({url} as HttpSource));
  const {controller, client, playback} = setup();
  playback.update(state('a')); await flush();
  let resolve: (value: {schema_version: string}) => void = () => {};
  client.reference.mockImplementationOnce(() => new Promise(done => {resolve = done;}));
  const changed = state('a'); changed.view = {...view('a'), layout: ref('c')};
  playback.update(changed);
  playback.select(result('a').trace, 1, null); await flush();
  resolve({schema_version: '1.0'}); await flush();
  expect(controller.setPresentation).toHaveBeenCalledExactlyOnceWith(null);
  playback.dispose();
});
it('supersedes an opening presentation when history selects the same attempt', async () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => ({url} as HttpSource));
  const {controller, client, playback} = setup();
  let resolve: (value: {schema_version: string}) => void = () => {};
  client.reference.mockImplementationOnce(() => new Promise(done => {resolve = done;}));
  playback.update(state('a')); await flush();
  playback.select(result('a').trace, 1, null); await flush();
  resolve({schema_version: '1.0'}); await flush();
  expect(controller.open).toHaveBeenCalledExactlyOnceWith({url: '/trace/a'}, 'Run · revision 1', null, undefined);
  playback.dispose();
});
it('supersedes a controller open that has exposed its source before preparing playback', async () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(async url => ({url} as HttpSource));
  const {controller, playback} = setup();
  let release: () => void = () => {};
  controller.open.mockImplementationOnce(async source => {
    controller.source = source as null;
    await new Promise<void>(resolve => {release = resolve;});
  });
  playback.update(state('a')); await flush();
  playback.select(result('a').trace, 1, null); await flush();
  // A second open advances the Controller's own generation and fences its old load.
  expect(controller.open).toHaveBeenCalledTimes(2);
  expect(controller.open).toHaveBeenLastCalledWith({url: '/trace/a'}, 'Run · revision 1', null, undefined);
  release(); await flush(); playback.dispose();
});

it('reconciles a missed live terminal notification from authoritative state', () => {
  vi.spyOn(HttpSource, 'open').mockImplementation(() => new Promise(() => {}));
  const {playback} = setup();
  const active = {...state('a'), results:[], view:null, last_good:null, active:{key:key('a'),revision:1,status:'running',details:{},prefix:result('a').trace}};
  playback.update(active);
  expect(playback.live?.state.terminal).toBeNull();
  playback.update({...active,active:{...active.active,status:'cancelled'}});
  expect(playback.live?.state.terminal).toBe('cancelled');
  playback.dispose();
});
