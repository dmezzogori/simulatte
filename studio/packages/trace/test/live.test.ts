import { createHash } from 'node:crypto';
import { expect, it } from 'vitest';
import { LiveRecording, PrefixSource, type PrefixDescriptor } from '../src/live';
import { openRecording } from '../src/recording';
import { buildTrace } from './build';
import type { ByteSource } from '../src/container';

const initial = {state: {cell: {$kind: 'cell', value: 0}}, cursor: [0, -1], manifest: {}};
const bytes = new Uint8Array(buildTrace(initial, [1, 2, 3].map((value) => ({
  snapshot: {cell: {$kind: 'cell', value: value - 1}},
  events: [[value, value - 1, 'tick', value, {}, [['set', 'cell', 'value', value]]]],
}))));
function boundaries() {
  const result: number[] = [];
  let offset = 12;
  while (offset < bytes.length - 16) {
    const length = new DataView(bytes.buffer).getUint32(offset);
    const type = bytes[offset + 4];
    offset += 9 + length;
    if (type === 3 || type === 6) result.push(offset);
  }
  result.push(bytes.length);
  return result;
}
function descriptor(length: number, generation: number): PrefixDescriptor {
  return {attempt: 'a'.repeat(32), generation, length,
    sha256: createHash('sha256').update(bytes.subarray(0, length)).digest('hex')};
}
const reads: number[] = [];
function source(d: PrefixDescriptor): ByteSource {
  return {size: d.length, read: async (offset, length) => {
    reads.push(offset);
    return bytes.slice(offset, Math.min(offset + length, d.length));
  }};
}
it('extends committed indexes without rescanning old bytes and preserves old views', async () => {
  const ends = boundaries();
  const live = await LiveRecording.open(descriptor(ends[0]!, 1), source);
  const first = live.snapshot();
  expect(first.historyEnd).toBeNull();
  for (let i = 1; i < ends.length; i++) {
    reads.length = 0;
    await live.extend(descriptor(ends[i]!, i + 1));
    expect(reads.every(offset => offset >= ends[i - 1]!)).toBe(true);
    const view = live.snapshot();
    const staticView = await openRecording(bytes.slice(0, ends[i]!).buffer);
    expect(view.trace.index).toEqual(staticView.trace.index);
    expect(view.historyEnd).toEqual(staticView.historyEnd);
    expect(await view.events()).toEqual(await staticView.events());
    expect(view.finiteRange).toEqual(staticView.finiteRange);
    expect(first.trace.index).toHaveLength(0);
    expect(first.historyEnd).toBeNull();
  }
  expect(live.generation).toBe(ends.length);
});
it('rejects corrupt, regressing and foreign-attempt prefixes without publishing them', async () => {
  const ends = boundaries();
  const live = await LiveRecording.open(descriptor(ends[0]!, 1), source);
  const old = live.snapshot();
  const next = descriptor(ends[1]!, 2);
  await expect(live.extend({...next, sha256: '0'.repeat(64)})).rejects.toThrow(/hash/i);
  await expect(live.extend({...next, attempt: 'b'.repeat(32)})).rejects.toThrow(/attempt/i);
  await expect(live.extend({...next, generation: 1})).rejects.toThrow(/generation/i);
  expect(live.snapshot()).toBe(old);
  await live.extend(next);
  expect(live.generation).toBe(2);
});
it('bounds immutable prefix reads and never exposes bytes from a later source', async () => {
  const d = descriptor(boundaries()[0]!, 1);
  const prefix = new PrefixSource(d, source(d));
  expect((await prefix.read(d.length - 1, 100)).length).toBe(1);
  expect(await prefix.read(d.length, 1)).toHaveLength(0);
  await expect(prefix.read(-1, 1)).rejects.toThrow();
  const abort = new AbortController(); abort.abort();
  await expect(prefix.read(0, 1, abort.signal)).rejects.toThrow();
});
it('commits header, INITIAL, late catalog, observer-only and KPI-only boundaries independently', async () => {
  const data = new Uint8Array(buildTrace(initial, [], {records: [
    [4, {epoch: 1, types: {log: {category: 'observer'}}, kinds: {}}],
    [9, {phase: 'simulation', first: [2, 1], last: [2, 1], epoch: 1,
      events: [[1, null, 'log', 2, {message: 'hello'}, []]]}],
    [7, {declarations: {'cell/value': {name: 'value', unit: 'jobs', kind: ['series'],
      observation: 'job', cohort: 'completed_in_window', aggregation: 'mean', clip: 'none',
      censoring: 'exclude', ema_reset: false, empty: null, description: ''}},
      series_display: {'cell/value': {interpolation: 'previous', missing: 'gap'}},
      samples: [[2, 3, 'cell/value', 7]], scalars: {}}],
  ]}));
  let offset = 12, generation = 0;
  let live: LiveRecording | undefined;
  while (offset < data.length - 16) {
    const type = data[offset + 4];
    offset += 9 + new DataView(data.buffer).getUint32(offset);
    if (type === 8) offset += 16;
    const d = {...descriptor(offset, ++generation), sha256: createHash('sha256').update(data.subarray(0, offset)).digest('hex')};
    const provider = (p: PrefixDescriptor) => ({size: p.length,
      read: async (at: number, length: number) => data.slice(at, Math.min(p.length, at + length))});
    if (!live) live = await LiveRecording.open(d, provider);
    else await live.extend(d);
    const reference = await openRecording(data.slice(0, offset).buffer);
    const view = live.snapshot();
    expect(view.catalog).toEqual(reference.catalog);
    expect(view.seriesDisplay).toEqual(reference.seriesDisplay);
    expect(view.trace.kpiDeclarations).toEqual(reference.trace.kpiDeclarations);
    expect(view.finiteRange).toEqual(reference.finiteRange);
    expect(await view.events()).toEqual(await reference.events());
    expect(await view.sampleBracket('cell/value', [3, 2])).toEqual(await reference.sampleBracket('cell/value', [3, 2]));
  }
});
it('rejects cut records and a CHUNK without its INDEX even with a valid prefix hash', async () => {
  const ends = boundaries();
  const live = await LiveRecording.open(descriptor(ends[0]!, 1), source);
  const chunkEnd = ends[0]! + 9 + new DataView(bytes.buffer).getUint32(ends[0]!);
  for (const length of [ends[0]! + 3, chunkEnd]) {
    await expect(live.extend(descriptor(length, 2))).rejects.toThrow();
    expect(live.generation).toBe(1);
  }
});
it('aborted or disposed extensions cannot replace the visible snapshot', async () => {
  const ends = boundaries();
  let release: (() => void) | undefined;
  let block = false;
  const live = await LiveRecording.open(descriptor(ends[0]!, 1), async d => {
    if (block) await new Promise<void>(resolve => {release = resolve;});
    return source(d);
  });
  const old = live.snapshot();
  block = true;
  const pending = live.extend(descriptor(ends[1]!, 2));
  live.dispose(); release!();
  await expect(pending).rejects.toThrow();
  expect(live.snapshot()).toBe(old);
  await expect(live.extend(descriptor(ends[1]!, 2))).rejects.toThrow(/disposed/);
});
it('shares the decoded chunk cache across immutable generations', async () => {
  const ends = boundaries();
  const live = await LiveRecording.open(descriptor(ends[1]!, 1), source);
  const old = live.snapshot();
  await old.trace.prepare([1, 1]);
  await live.extend(descriptor(ends[2]!, 2));
  await live.snapshot().trace.prepare([2, 2]);
  expect(old.trace.cacheStats).toEqual(live.snapshot().trace.cacheStats);
  expect(old.trace.stateAt([1, 1]).cell?.value).toBe(1);
  expect(live.snapshot().trace.stateAt([2, 2]).cell?.value).toBe(2);
});
it('unpublished candidate chunks cannot poison a later candidate sharing the last good cache', async () => {
  const {Trace} = await import('../src/trace');
  const length = boundaries()[0]!;
  const base = await Trace.committed(source(descriptor(length, 1)));
  const candidate = await Trace.committed(source(descriptor(boundaries()[1]!, 2)), {}, base);
  await candidate.prepare([1, 1]);
  const alternate = new Uint8Array(buildTrace(initial, [{snapshot: initial.state,
    events: [[1, 0, 'tick', 1, {}, [['set', 'cell', 'value', 9]]]],
  }]));
  let end = length;
  end += 9 + new DataView(alternate.buffer).getUint32(end);
  end += 9 + new DataView(alternate.buffer).getUint32(end);
  const other = await Trace.committed({size: end, read: async (at, n) => alternate.slice(at, at + n)}, {}, base);
  expect((await other.preparedState([1, 1])).cell?.value).toBe(9);
  expect(candidate.stateAt([1, 1]).cell?.value).toBe(1);
});
it('limits all prefix I/O to two reads, including overlapping metadata and replay requests', async () => {
  const d = descriptor(boundaries()[0]!, 1);
  let active = 0, peak = 0;
  const releases: (() => void)[] = [];
  const prefix = new PrefixSource(d, {size: d.length, read: async (offset, length) => {
    peak = Math.max(peak, ++active);
    await new Promise<void>(resolve => releases.push(resolve));
    active--;
    return bytes.slice(offset, offset + length);
  }});
  const requests = [prefix.read(0, 1), prefix.read(1, 1), prefix.read(2, 1)];
  expect(peak).toBe(2);
  releases.shift()!();
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(peak).toBe(2);
  releases.splice(0).forEach(release => release());
  await Promise.all(requests);
});
it('cancels a queued prefix read without consuming an I/O slot', async () => {
  const d = descriptor(boundaries()[0]!, 1);
  const releases: (() => void)[] = [];
  let reads = 0;
  const prefix = new PrefixSource(d, {size: d.length, read: async (offset, length) => {
    reads++;
    await new Promise<void>(resolve => releases.push(resolve));
    return bytes.slice(offset, offset + length);
  }});
  const a = prefix.read(0, 1), b = prefix.read(1, 1);
  const controller = new AbortController();
  const queued = prefix.read(2, 1, controller.signal);
  controller.abort();
  await expect(queued).rejects.toThrow();
  expect(reads).toBe(2);
  releases.forEach(release => release()); await Promise.all([a, b]);
});
it('keeps four decoded chunks across retained generation views', async () => {
  const data = new Uint8Array(buildTrace(initial, Array.from({length: 8}, (_, i) => ({
    snapshot: initial.state, events: [[i, i, 'tick', i, {}, []]],
  }))));
  let offset = 12, generation = 0, live: LiveRecording | undefined;
  const retained: ReturnType<LiveRecording['snapshot']>[] = [];
  while (offset < data.length - 16) {
    const type = data[offset + 4]; offset += 9 + new DataView(data.buffer).getUint32(offset);
    if (type !== 3 && type !== 6) continue;
    const d = {...descriptor(offset, ++generation), sha256: createHash('sha256').update(data.subarray(0, offset)).digest('hex')};
    const provider = (p: PrefixDescriptor) => ({size: p.length,
      read: async (at: number, n: number) => data.slice(at, Math.min(at + n, p.length))});
    if (!live) live = await LiveRecording.open(d, provider); else await live.extend(d);
    const view = live.snapshot(); retained.push(view);
    if (view.trace.index.length) await view.trace.prepare(view.trace.cursorRange![1]);
    for (const old of retained) {
      expect(old.trace.cacheStats.chunks).toBeLessThanOrEqual(4);
      expect(old.trace.cacheStats.estimatedBytes).toBeLessThanOrEqual(128 << 20);
      expect(old.trace.cacheStats.activeReads).toBeLessThanOrEqual(2);
    }
  }
});
it('passes cancellation through terminal trailer reads and retains the previous view', async () => {
  const ends = boundaries();
  let entered!: () => void;
  const waiting = new Promise<void>(resolve => {entered = resolve;});
  const live = await LiveRecording.open(descriptor(ends[2]!, 1), d => ({size: d.length,
    read: async (offset, length, signal) => {
      if (d.length === bytes.length && offset === bytes.length - 16) {
        entered();
        await new Promise<void>((resolve, reject) => {
          signal?.addEventListener('abort', () => reject(signal.reason), {once: true});
          // A missing signal is an immediate test failure, not a hung test.
          if (!signal) reject(new Error('missing trailer cancellation'));
        });
      }
      return bytes.slice(offset, Math.min(offset + length, d.length));
    }}));
  const old = live.snapshot(), controller = new AbortController();
  const pending = live.extend(descriptor(bytes.length, 2), controller.signal);
  await waiting; controller.abort();
  await expect(pending).rejects.toMatchObject({name: 'AbortError'});
  expect(live.snapshot()).toBe(old);
});
