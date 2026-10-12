import {createHash} from 'node:crypto';
import {expect, it} from 'vitest';
import {LiveSession} from '../src/live-session';
import {buildTrace} from '../../../packages/trace/test/build';
import type {PrefixDescriptor} from '@simulatte/trace';
const data = new Uint8Array(buildTrace({state: {}, cursor: [0, -1], manifest: {}}, [
  {snapshot: {}, events: [[0, 0, 'tick', 1, {}, []]]},
  {snapshot: {}, events: [[1, 1, 'tick', 2, {}, []]]},
]));
const ends: number[] = [];
for (let i = 12; i < data.length - 16;) {
  const type = data[i + 4]; i += 9 + new DataView(data.buffer).getUint32(i);
  if (type === 3 || type === 6) ends.push(i);
}
ends.push(data.length);
const attempt = 'a'.repeat(32);
const prefix = (i: number, id = attempt): PrefixDescriptor => ({attempt: id, generation: i + 1,
  length: ends[i]!, sha256: createHash('sha256').update(data.subarray(0, ends[i])).digest('hex')});
const provider = (d: PrefixDescriptor) => ({size: d.length,
  read: async (offset: number, length: number) => data.slice(offset, Math.min(d.length, offset + length))});
const clock = {request: () => 1, cancel: () => {}};
it('coalesces updates, follows only explicitly, and derives terminal state from coordinator', async () => {
  const viewed: number[] = [];
  const session = new LiveSession(provider, {clock, onChange: state => {
    if (state.generation) viewed.push(state.generation);
  }});
  session.reset(attempt, 'revision-1');
  session.update(prefix(0)); session.update(prefix(1)); session.update(prefix(2));
  await session.idle();
  expect(session.state.generation).toBe(3);
  expect(session.state.following).toBe(false);
  expect(viewed).not.toContain(2);
  await session.follow();
  expect(session.state.frame?.time).toBe(2);
  expect(session.state.status).toBe('buffering');
  await session.seek({mode: 'continuous', time: 1, cursor: null});
  expect(session.state.following).toBe(false);
  session.update(prefix(3)); await session.idle();
  expect(session.state.frame?.time).toBe(1);
  expect(session.state.terminal).toBeNull();
  session.terminal('completed');
  expect(session.state.terminal).toBe('completed');
  session.dispose();
});
it('late reads cannot publish after an attempt/revision reset', async () => {
  let release: (() => void) | undefined;
  const session = new LiveSession(async d => {
    if (d.attempt === attempt) await new Promise<void>(resolve => {release = resolve;});
    return provider(d);
  }, {clock});
  session.reset(attempt, 'old'); session.update(prefix(0));
  const old = session.idle();
  const other = 'b'.repeat(32);
  session.reset(other, 'new'); session.update(prefix(1, other));
  await session.idle(); release!(); await old;
  expect(session.state.attempt).toBe(other);
  expect(session.state.revision).toBe('new');
  expect(session.state.generation).toBe(2);
  session.invalidate();
  expect(session.state.status).toBe('invalidated');
  expect(session.state.frame).toBeNull();
  session.dispose();
});
it('manual seek supersedes an in-flight Follow frame and failure invalidates provisional data', async () => {
  const session = new LiveSession(provider, {clock});
  session.reset(attempt, 'r'); session.update(prefix(2)); await session.idle();
  const following = session.follow();
  const seeking = session.seek({mode: 'continuous', time: 1, cursor: null});
  await Promise.all([following, seeking]);
  expect(session.state.following).toBe(false);
  expect(session.state.frame?.time).toBe(1);
  await session.step(1);
  expect(session.state.frame?.time).toBe(2);
  session.terminal('failed');
  session.update(prefix(3)); await session.idle();
  expect(session.state.status).toBe('invalidated');
  expect(session.state.recording).toBeNull();
  expect(session.state.terminal).toBe('failed');
  session.dispose();
});
it('bad extensions preserve the previous view and a later valid update recovers', async () => {
  const session = new LiveSession(provider, {clock});
  session.reset(attempt, 'r'); session.update(prefix(1)); await session.idle();
  const old = session.state.recording;
  session.update({...prefix(2), sha256: '0'.repeat(64)}); await session.idle();
  expect(session.state.status).toBe('error');
  expect(session.state.recording).toBe(old);
  session.update(prefix(2)); await session.idle();
  expect(session.state.generation).toBe(3);
  expect(session.state.status).toBe('paused');
  session.dispose();
});
it('late terminal messages and controls cannot revive a disposed or invalidated session', async () => {
  const session = new LiveSession(provider, {clock});
  session.reset(attempt, 'r'); session.update(prefix(1)); await session.idle();
  await session.follow(); session.invalidate(); session.terminal('completed');
  expect(session.state.status).toBe('invalidated');
  session.dispose(); session.terminal('completed');
  await expect(session.follow()).rejects.toThrow(/disposed/);
  expect(session.state.status).toBe('disposed');
});
it('keeps playback controls across prefixes and detaches frame listeners', async () => {
  const session = new LiveSession(provider, {clock});
  session.reset(attempt, 'r');
  session.rate = 2;
  session.setHidden(true);
  let frames = 0;
  const off = session.subscribe(() => frames++);
  session.update(prefix(1)); await session.idle();
  expect(session.rate).toBe(2);
  await session.seekTime(0.5);
  expect(session.frame?.time).toBe(0.5);
  session.update(prefix(2)); await session.idle();
  expect(session.rate).toBe(2);
  expect(session.frame?.time).toBe(0.5);
  const count = frames; off();
  await session.seekTime(1);
  expect(frames).toBe(count);
  expect(() => {session.rate = NaN;}).toThrow(/rate/);
  session.dispose();
});
