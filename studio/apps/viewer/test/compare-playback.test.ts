import {afterEach, expect, it, vi} from 'vitest';
import {HttpSource} from '@simulatte/trace';
import type {Clock} from '@simulatte/playback';
import {Scene, liveSceneCount} from '@simulatte/scene';
import {ComparePlayback, CompareRefused, FrameClock, type CompareScene} from '../src/compare-playback';
import type {Prefix} from '../src/studio-client';
import {buildTrace, HEADER} from '../../../packages/trace/test/build';

const initial = {state: {}, cursor: [0, -1], manifest: {}};
const tick = (seq: number, t: number) => [seq, seq, 'tick', t, {}, []];
// Side A has events at t = 1, 2, 3; side B at t = 1.5, 2.5, 4.
const bytes: Record<string, Uint8Array> = {
  '/a': new Uint8Array(buildTrace(initial, [{snapshot: {}, events: [tick(0, 1), tick(1, 2)]}, {snapshot: {}, events: [tick(2, 3)]}])),
  '/b': new Uint8Array(buildTrace(initial, [{snapshot: {}, events: [tick(0, 1.5), tick(1, 2.5)]}, {snapshot: {}, events: [tick(2, 4)]}])),
  '/kpi': new Uint8Array(buildTrace(initial, [], {header: {...HEADER, level: 'kpi'}})),
};
const prefix = (url: string): Prefix => ({attempt: url.slice(1).padEnd(32, '0'), generation: 1, length: bytes[url]!.length, sha256: '0'.repeat(64), url});
const headers = {'X-Simulatte-Session': 'secret'};

/** A fake animation-frame source: callbacks run only when the test advances a frame. */
class Frames implements Clock {
  at = 1000;
  requests = 0;
  #id = 0;
  #due = new Map<number, (now: number) => void>();
  request(callback: (now: number) => void): number {this.requests++; this.#due.set(++this.#id, callback); return this.#id;}
  cancel(id: number): void {this.#due.delete(id);}
  get pending(): number {return this.#due.size;}
  frame(ms: number): void {
    this.at += ms;
    const due = [...this.#due.values()]; this.#due.clear();
    for (const callback of due) callback(this.at);
  }
}
const opened: {url: string; headers: Readonly<Record<string, string>>}[] = [];
function sources(delay: (url: string) => Promise<void> = async () => {}) {
  opened.length = 0;
  vi.spyOn(HttpSource, 'open').mockImplementation(async (url, given = {}) => {
    opened.push({url, headers: given});
    await delay(url);
    const data = bytes[url]!;
    return {size: data.length, read: async (offset: number, length: number) => data.slice(offset, offset + length)} as unknown as HttpSource;
  });
}
const fakeScene = (created: HTMLElement[]) => async (host: HTMLElement): Promise<CompareScene> => {
  created.push(host);
  const counted = new Scene(); // A real scene for liveScenes accounting; it is never initialized or drawn.
  return {render: () => {}, setPlaying: () => {}, resetTransfers: () => {}, dispose: () => counted.dispose()};
};
const host = () => ({}) as HTMLElement;
const times = (compare: ComparePlayback) => [compare.side(0)?.playback.frame?.time, compare.side(1)?.playback.frame?.time];
afterEach(() => vi.restoreAllMocks());

it('shares one animation frame between both playbacks', () => {
  const frames = new Frames(), clock = new FrameClock(frames);
  const seen: number[] = [];
  const first = clock.request(now => seen.push(now)); clock.request(now => seen.push(-now));
  expect(frames.requests).toBe(1);
  clock.cancel(first); expect(frames.pending).toBe(1);
  frames.frame(16);
  expect(seen).toEqual([-1016]);
  expect((clock as Clock).now).toBeUndefined(); // Playback anchors on the frame timestamp, never on a later reading.
});

it('advances two sessions on one clock: one frame moves both to the same time', async () => {
  sources();
  const frames = new Frames();
  const compare = new ComparePlayback(headers, {clock: frames, scene: fakeScene([])});
  await compare.load(0, prefix('/a'), host());
  await compare.load(1, prefix('/b'), host());
  expect(opened).toEqual([{url: '/a', headers}, {url: '/b', headers}]);
  expect(times(compare)).toEqual([0, 0]);
  await compare.play();
  expect(compare.playing).toBe(true);
  expect(frames.pending).toBe(1); // Both playbacks wait on the single shared request.
  frames.frame(16); // The first frame anchors both on one timestamp.
  await vi.waitFor(() => expect(frames.pending).toBe(1));
  for (const [ms, time] of [[500, 0.5], [250, 0.75], [1000, 1.75]] as const) {
    const requests = frames.requests;
    frames.frame(ms);
    await vi.waitFor(() => expect(times(compare)).toEqual([time, time]));
    await vi.waitFor(() => expect(frames.pending).toBe(1));
    expect(frames.requests).toBe(requests + 1);
  }
  compare.rate = 2; // A rate change restarts both in one turn and keeps them together.
  await vi.waitFor(() => expect(frames.pending).toBe(1));
  frames.frame(16); await vi.waitFor(() => expect(frames.pending).toBe(1));
  frames.frame(500);
  await vi.waitFor(() => expect(times(compare)).toEqual([2.75, 2.75]));
  await compare.pause();
  expect(compare.playing).toBe(false);
  expect(new Set(times(compare)).size).toBe(1);
  compare.dispose();
  expect(frames.pending).toBe(0);
});

it('seeking or stepping either side seeks both', async () => {
  sources();
  const compare = new ComparePlayback(headers, {clock: new Frames(), scene: fakeScene([])});
  await compare.load(0, prefix('/a'), host());
  await compare.load(1, prefix('/b'), host());
  await compare.seekTime(2.25);
  expect(times(compare)).toEqual([2.25, 2.25]);
  // A seek issued on one side's own Playback is followed by the other side.
  await compare.side(1)!.playback.seekTime(1.25);
  await vi.waitFor(() => expect(times(compare)).toEqual([1.25, 1.25]));
  await compare.side(0)!.playback.seekTime(2.5);
  await vi.waitFor(() => expect(times(compare)).toEqual([2.5, 2.5]));
  // Steps go to the nearest event of either recording: A's next is 3, B's is 4.
  await compare.step(1);
  expect(times(compare)).toEqual([3, 3]);
  await compare.step(-1);
  expect(times(compare)).toEqual([2.5, 2.5]);
  await compare.step(-1);
  expect(times(compare)).toEqual([2, 2]);
  // Past A's end A holds its last time; B continues.
  await compare.seekTime(3.5);
  expect(times(compare)).toEqual([3, 3.5]);
  expect(compare.range).toEqual([0, 4]);
  compare.dispose();
});

it('disposing the compare view keeps the main scene and its global resources', async () => {
  sources();
  const before = liveSceneCount();
  const main = new Scene(); // The main viewer's scene stays live throughout.
  const hosts: HTMLElement[] = [];
  const compare = new ComparePlayback(headers, {clock: new Frames(), scene: fakeScene(hosts)});
  const [a, b] = [host(), host()];
  await compare.load(0, prefix('/a'), a);
  await compare.load(1, prefix('/b'), b);
  expect(hosts).toEqual([a, b]);
  expect(liveSceneCount()).toBe(before + 3);
  // Replacing a side disposes only that side's scene.
  await compare.load(1, prefix('/a'), b);
  expect(liveSceneCount()).toBe(before + 3);
  compare.dispose();
  // Scene.dispose passes releaseGlobalResources only when the count reaches zero: the main scene keeps it above.
  expect(liveSceneCount()).toBe(before + 1);
  expect(compare.side(0)).toBeNull();
  main.dispose();
  expect(liveSceneCount()).toBe(before);
});

it('refuses a KPI-only recording with a typed reason and creates no scene', async () => {
  sources();
  const hosts: HTMLElement[] = [];
  const compare = new ComparePlayback(headers, {clock: new Frames(), scene: fakeScene(hosts)});
  const refused = await compare.load(0, prefix('/kpi'), host()).catch((error: unknown) => error);
  expect(refused).toBeInstanceOf(CompareRefused);
  expect((refused as CompareRefused).reason).toEqual({code: 'kpi_only', level: 'kpi'});
  expect(hosts).toEqual([]);
  expect(compare.side(0)).toBeNull();
  compare.dispose();
});

it('fences a slow selection superseded on the same side', async () => {
  let release: () => void = () => {};
  sources(url => url === '/b' ? new Promise(done => {release = done;}) : Promise.resolve());
  const hosts: HTMLElement[] = [];
  const compare = new ComparePlayback(headers, {clock: new Frames(), scene: fakeScene(hosts)});
  const slow = compare.load(0, prefix('/b'), host()).catch((error: unknown) => error);
  await compare.load(0, prefix('/a'), host());
  release();
  expect(((await slow) as DOMException).name).toBe('AbortError');
  expect(hosts).toHaveLength(1);
  expect(compare.side(0)?.attempt).toBe(prefix('/a').attempt);
  compare.dispose();
  await expect(compare.load(1, prefix('/a'), host())).rejects.toThrow('disposed');
});

it('pauses on a hidden page and resumes together', async () => {
  sources();
  const frames = new Frames();
  const compare = new ComparePlayback(headers, {clock: frames, scene: fakeScene([])});
  await compare.load(0, prefix('/a'), host());
  await compare.load(1, prefix('/b'), host());
  await compare.play();
  compare.setHidden(true);
  expect(compare.playing).toBe(false);
  compare.setHidden(false);
  await vi.waitFor(() => expect(compare.playing).toBe(true));
  compare.dispose();
});
