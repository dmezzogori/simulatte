import {readFileSync} from 'node:fs';
import {expect, it} from 'vitest';
import {openRecording, type LayoutBundle} from '@simulatte/trace';
import {Playback, Session, pinPresentation} from '../src/index';
const fixture = () => openRecording(new Uint8Array(readFileSync(new URL('../../../../tests/fixtures/traces/generated/layout_full.simtrace', import.meta.url))).buffer);
it('pins a presentation layout separately from the immutable trace and simulation state', async () => {
  const recording = await fixture();
  const original = recording.trace.layout!;
  const overlay = structuredClone(original) as Record<string, any>;
  const placements = overlay.resolved.placements;
  const id = Object.keys(placements)[0]!;
  placements[id].visual = {...placements[id].visual, label: 'reviewed presentation'};
  overlay.full_hash = 'd'.repeat(64);
  const pinned = pinPresentation(recording, overlay as LayoutBundle)!;
  expect(pinPresentation(recording, pinned)).toBe(pinned);
  placements[id].visual.label = 'later edit';
  const session = new Session(recording, pinned);
  const frame = await session.prepare({mode: 'activation', time: recording.trace.activation![0], cursor: recording.trace.activation});
  expect((frame.layout!.resolved as any).placements[id].visual.label).toBe('reviewed presentation');
  expect(frame.state).toEqual(recording.trace.initialState);
  expect(recording.trace.layout).toBe(original);
  expect(recording.trace.layout!.full_hash).not.toBe('d'.repeat(64));
  expect(Object.isFrozen((pinned.resolved as any).placements[id].visual)).toBe(true);
  session.setPresentation(null);
  expect((await session.prepare(frame)).layout).toBe(original);
  session.dispose();
});
it('rejects a physical mismatch, unknown schema, cycles and overlays without a recorded layout', async () => {
  const recording = await fixture();
  const original = recording.trace.layout!;
  expect(() => pinPresentation(recording, {...original, physical_hash: '0'.repeat(64)})).toThrow(/physical/);
  expect(() => pinPresentation(recording, {...original, schema_version: 'next'})).toThrow(/schema/);
  const cycle = {...original} as any; cycle.resolved = cycle;
  expect(() => pinPresentation(recording, cycle)).toThrow(/cycle|depth/);
  expect(() => pinPresentation({...recording, trace: {...recording.trace, layout: null}} as any, original)).toThrow(/recorded layout/);
});

it('refreshes presentation without stopping playback and a later manual seek wins', async () => {
  const bytes = new Uint8Array(readFileSync(new URL('../../../../tests/fixtures/traces/generated/layout_full.simtrace', import.meta.url)));
  const recording = await openRecording(bytes.buffer);
  const playback = new Playback(new Session(recording), {request: () => 1, cancel: () => {}});
  await playback.seekTime(recording.finiteRange![0]); playback.play();
  await playback.setPresentation(recording.trace.layout!);
  expect(playback.status).toBe('playing');
  const refresh = playback.setPresentation(null);
  const manual = playback.seekTime(recording.finiteRange![0]);
  await Promise.all([refresh, manual]);
  expect(playback.status).toBe('paused'); playback.dispose();
});
