import {readFileSync, readdirSync, existsSync} from 'node:fs';
import {describe, expect, it} from 'vitest';
import {openRecording} from '@simulatte/trace';
import {Playback, Session} from '../src/index';
import {
  PresentationError, applyPresentation, capturePresentation, enforceRange, validatePresentation,
  type PresentationManifest,
} from '../src/presentation-manifest';

const fixtures = new URL('../../../../tests/fixtures/presentations/', import.meta.url);
const read = (name: string) => JSON.parse(readFileSync(new URL(name, fixtures), 'utf8')) as unknown;
const recording = () => openRecording(new Uint8Array(readFileSync(new URL('../../../../tests/fixtures/traces/generated/layout_full.simtrace', import.meta.url))).buffer);
const clock = () => ({request: (callback: (now: number) => void) => setTimeout(() => callback(performance.now()), 0) as unknown as number, cancel: (id: number) => clearTimeout(id)});

describe('presentation-v1 manifest', () => {
  it('accepts the shared valid fixtures and returns frozen copies', () => {
    for (const name of ['valid-minimal.json', 'valid-full.json']) {
      const manifest = validatePresentation(read(name));
      expect(manifest.schema).toBe(1);
      expect(Object.isFrozen(manifest)).toBe(true);
      expect(Object.isFrozen(manifest.evidence)).toBe(true);
      expect(manifest.layout.schema_version).toBe('1.0');
    }
    const full = validatePresentation(read('valid-full.json'));
    expect(full.id).toBe('0f6a9e2c-7d3b-4c1e-9b5a-2d8f4e6c1a7b');
    expect(full.revision).toBe(3);
    expect(full.evidence.map(e => e.kind)).toEqual(['number', 'table', 'figure']);
    expect(full.evidence[1]!.selection).toEqual({rows: {offset: 0, limit: 10, columns: ['family', 'lateness']}});
    expect(full.evidence[0]!.labels.sample).toEqual({planned: 24, available: 8, partial: true});
    const minimal = validatePresentation(read('valid-minimal.json'));
    expect(minimal.id).toBeUndefined();
    expect(minimal.visibility).toEqual({entities: null, labels: null});
  });
  it('rejects every shared invalid fixture at the expected path', () => {
    const names = readdirSync(fixtures).filter(name => name.startsWith('invalid-') && name.endsWith('.json'));
    expect(names.length).toBeGreaterThan(15);
    for (const name of names) {
      const expected = new URL(name.replace(/\.json$/, '.expect.txt'), fixtures);
      let error: unknown;
      try {validatePresentation(read(name));} catch (e) {error = e;}
      expect(error, name).toBeInstanceOf(PresentationError);
      if (existsSync(expected)) expect((error as PresentationError).path, name).toBe(readFileSync(expected, 'utf8').trim());
    }
  });
  it('rejects non-objects, nested unknown keys and over-long lists', () => {
    expect(() => validatePresentation(null)).toThrow(PresentationError);
    expect(() => validatePresentation([])).toThrow(PresentationError);
    const full = read('valid-full.json') as Record<string, unknown>;
    expect(() => validatePresentation({...full, visibility: {entities: null, labels: null, extra: 1}})).toThrow(/visibility\.extra/);
    expect(() => validatePresentation({...full, annotations: Array.from({length: 51}, (_, i) => ({id: `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`, text: 'x', time: null}))})).toThrow(/annotations/);
    expect(() => validatePresentation({...full, evidence: Array(13).fill((full.evidence as unknown[])[0])})).toThrow(/evidence/);
    expect(() => validatePresentation({...full, visibility: {entities: Array.from({length: 10001}, (_, i) => String(i)), labels: null}})).toThrow(/visibility\.entities/);
  });
});

class FakeScene {
  bounds: number[] | null = null;
  visibility: {entities: ReadonlySet<string> | null; labels: ReadonlySet<string> | null} | null = null;
  fitBounds(bounds: readonly number[]) {this.bounds = [...bounds];}
  setVisibility(visibility: {entities: ReadonlySet<string> | null; labels: ReadonlySet<string> | null}) {this.visibility = visibility;}
  worldBounds(): [number, number, number, number] {return [-1.5, -2, 10.25, 3.5];}
}
describe('apply and capture', () => {
  it('applies camera, visibility, series, rate and start time, then captures the same view back', async () => {
    const rec = await recording();
    const session = new Session(rec);
    const playback = new Playback(session, clock());
    const scene = new FakeScene();
    const manifest = validatePresentation(read('valid-full.json'));
    await applyPresentation(scene, session, playback, manifest);
    expect(scene.bounds).toEqual([-2, -3, 12.5, 6]);
    expect([...scene.visibility!.entities!]).toEqual(['station', 'layout:7b2269223a302c226a223a302c226b696e64223a226c617474696365227d']);
    expect([...scene.visibility!.labels!]).toEqual(['station']);
    expect(playback.rate).toBe(2);
    expect(playback.frame?.time).toBe(0);
    const captured = capturePresentation(scene, session, playback, {
      name: manifest.name, notes: manifest.notes, recording: manifest.recording, layout: manifest.layout,
      visibility: manifest.visibility, measurements: manifest.measurements, time: manifest.time,
      annotations: manifest.annotations, evidence: manifest.evidence,
    });
    expect(captured.camera.bounds).toEqual([-1.5, -2, 10.25, 3.5]);
    expect(captured.rate).toBe(2);
    expect(captured.schema).toBe(1);
    expect(captured.id).toBeUndefined();
    expect(captured.evidence).toEqual(manifest.evidence);
    expect(typeof captured.created).toBe('number');
    // A roundtrip through validation is byte-stable apart from the capture time.
    expect(validatePresentation(JSON.parse(JSON.stringify(captured)))).toEqual(captured);
    playback.dispose();
    session.dispose();
  });
  it('refuses a selection that would not validate', async () => {
    const rec = await recording();
    const session = new Session(rec);
    const playback = new Playback(session, clock());
    const manifest = validatePresentation(read('valid-minimal.json'));
    expect(() => capturePresentation(new FakeScene(), session, playback, {...manifest, name: ''})).toThrow(PresentationError);
    expect(() => capturePresentation(new FakeScene(), session, playback, {...manifest, measurements: ['a', 'a']})).toThrow(/measurements/);
    playback.dispose();
    session.dispose();
  });
  it('pauses playback at the end of the saved range and keeps seeks inside it', async () => {
    const rec = await recording();
    const session = new Session(rec);
    const playback = new Playback(session, clock());
    const stop = enforceRange(playback, {start: 0.25, end: 0.5});
    await playback.seekTime(0.9);
    expect(playback.frame?.time).toBe(0.5);
    await playback.seekTime(0.1);
    expect(playback.frame?.time).toBe(0.25);
    stop();
    await playback.seekTime(0.9);
    expect(playback.frame?.time).toBe(0.9);
    playback.dispose();
    session.dispose();
  });
});
