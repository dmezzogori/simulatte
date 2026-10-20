/** presentation-v1: a saved view of one recording with its supporting evidence (R1/R5).
 * The TypeScript mirror of `simulatte.presentations.schema`; both accept and reject the
 * shared fixtures under `tests/fixtures/presentations` identically. */
import {layoutOf, type LayoutBundle} from '@simulatte/trace';

export interface PlaybackRange {start: number; end: number;}
export type Bounds = readonly [number, number, number, number];
export type EvidenceKind = 'number' | 'table' | 'distribution' | 'figure' | 'image';
export interface EvidenceLabels {
  readonly name: string;
  readonly unit: string | null;
  readonly population: string | null;
  readonly sample: {readonly planned: number; readonly available: number; readonly partial: boolean};
  readonly convention: string;
  readonly source: {readonly analysis_name: string; readonly snapshot_hash: string; readonly execution_id: string};
}
export type EvidenceChoice =
  | {readonly rows: {readonly offset: number; readonly limit: number; readonly columns: readonly string[]}}
  | {readonly figure: {readonly width: number; readonly height: number}}
  | Readonly<Record<never, never>>;
export interface EvidenceSelection {
  readonly id: string;
  readonly execution_id: string;
  readonly output_id: string;
  readonly content_hash: string;
  readonly kind: EvidenceKind;
  readonly selection: EvidenceChoice;
  /** Copied from the output metadata when the evidence was chosen; never edited afterwards. */
  readonly labels: EvidenceLabels;
}
export interface Annotation {readonly id: string; readonly text: string; readonly time: number | null;}
export interface PresentationManifest {
  readonly schema: 1;
  readonly id?: string;
  readonly revision?: number;
  readonly name: string;
  readonly notes: string;
  readonly recording: {readonly attempt_id: string; readonly request_id: string; readonly trace_sha256: string};
  readonly layout: LayoutBundle;
  readonly camera: {readonly bounds: Bounds};
  readonly visibility: {readonly entities: readonly string[] | null; readonly labels: readonly string[] | null};
  readonly measurements: readonly string[];
  readonly time: {readonly start: number; readonly end: number};
  readonly rate: number;
  readonly annotations: readonly Annotation[];
  readonly evidence: readonly EvidenceSelection[];
  readonly created: number;
}
/** Everything a capture needs apart from the live camera and rate. */
export type PresentationSelection = Omit<PresentationManifest, 'schema' | 'camera' | 'rate' | 'created'> & {
  readonly rate?: number; readonly created?: number;
};
export const LIMITS = {name: 200, notes: 4000, ids: 10_000, measurements: 8, annotations: 50, annotationText: 2000, evidence: 12,
  rows: 50, columns: 12, figureWidth: [320, 3840], figureHeight: [180, 2160], rate: 1024} as const;
export class PresentationError extends TypeError {
  constructor(message: string, readonly path: string) {super(`${path}: ${message}`);}
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HEX32 = /^[0-9a-f]{32}$/, HEX64 = /^[0-9a-f]{64}$/;
const KINDS: readonly EvidenceKind[] = ['number', 'table', 'distribution', 'figure', 'image'];
const fail = (path: string, message: string): never => {throw new PresentationError(message, path);};
const object = (value: unknown, path: string, keys: readonly string[], optional: readonly string[] = []): Record<string, unknown> => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return fail(path, 'must be an object');
  const record = value as Record<string, unknown>;
  for (const key of Object.keys(record)) if (!keys.includes(key) && !optional.includes(key)) fail(path ? `${path}.${key}` : key, 'unknown key');
  for (const key of keys) if (!(key in record)) fail(path ? `${path}.${key}` : key, 'missing');
  return record;
};
const string = (value: unknown, path: string, min: number, max: number): string => {
  if (typeof value !== 'string') return fail(path, 'must be a string');
  const length = Array.from(value).length;
  if (length < min || length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) fail(path, `must be plain text of ${min} to ${max} characters`);
  return value;
};
const number = (value: unknown, path: string): number => typeof value === 'number' && Number.isFinite(value) ? value : fail(path, 'must be a finite number');
const integer = (value: unknown, path: string, min: number, max = Number.MAX_SAFE_INTEGER): number => {
  const n = number(value, path);
  if (!Number.isInteger(n) || n < min || n > max) fail(path, `must be an integer from ${min} to ${max}`);
  return n;
};
const hex = (value: unknown, path: string, pattern: RegExp, what: string): string => typeof value === 'string' && pattern.test(value) ? value : fail(path, `must be a ${what}`);
const list = (value: unknown, path: string, max: number, what: string): unknown[] =>
  Array.isArray(value) && value.length <= max ? (value as unknown[]) : fail(path, `must list at most ${max} ${what}`);
const uuid = (value: unknown, path: string): string => typeof value === 'string' && UUID.test(value) ? value : fail(path, 'must be a UUID');
const idList = (value: unknown, path: string): readonly string[] | null => {
  if (value === null) return null;
  const ids = list(value, path, LIMITS.ids, 'ids').map((item, i) => string(item, `${path}[${i}]`, 1, 1024));
  if (new Set(ids).size !== ids.length) fail(path, 'ids must be unique');
  return Object.freeze(ids);
};
const labels = (value: unknown, path: string): EvidenceLabels => {
  const record = object(value, path, ['name', 'unit', 'population', 'sample', 'convention', 'source']);
  const nullable = (v: unknown, p: string) => (v === null ? null : string(v, p, 0, 1024));
  const sample = object(record['sample'], `${path}.sample`, ['planned', 'available', 'partial']);
  if (typeof sample['partial'] !== 'boolean') fail(`${path}.sample.partial`, 'must be a boolean');
  const source = object(record['source'], `${path}.source`, ['analysis_name', 'snapshot_hash', 'execution_id']);
  return Object.freeze({
    name: string(record['name'], `${path}.name`, 0, 1024),
    unit: nullable(record['unit'], `${path}.unit`),
    population: nullable(record['population'], `${path}.population`),
    sample: Object.freeze({planned: integer(sample['planned'], `${path}.sample.planned`, 0), available: integer(sample['available'], `${path}.sample.available`, 0), partial: sample['partial'] as boolean}),
    convention: string(record['convention'], `${path}.convention`, 0, 1024),
    source: Object.freeze({
      analysis_name: string(source['analysis_name'], `${path}.source.analysis_name`, 0, 1024),
      snapshot_hash: hex(source['snapshot_hash'], `${path}.source.snapshot_hash`, HEX64, '64-hex hash'),
      execution_id: uuid(source['execution_id'], `${path}.source.execution_id`),
    }),
  });
};
const evidence = (value: unknown, path: string): EvidenceSelection => {
  const record = object(value, path, ['id', 'execution_id', 'output_id', 'content_hash', 'kind', 'selection', 'labels']);
  const kind = record['kind'];
  if (typeof kind !== 'string' || !KINDS.includes(kind as EvidenceKind)) fail(`${path}.kind`, `must be one of ${KINDS.join(', ')}`);
  const raw = record['selection'];
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) fail(`${path}.selection`, 'must be an object');
  const keys = Object.keys(raw as object);
  let selection: EvidenceChoice;
  if (kind === 'table') {
    if (keys.join() !== 'rows') fail(`${path}.selection`, 'a table selects rows');
    const rows = object((raw as Record<string, unknown>)['rows'], `${path}.selection.rows`, ['offset', 'limit', 'columns']);
    const columns = list(rows['columns'], `${path}.selection.rows.columns`, LIMITS.columns, 'columns');
    const names = columns.map((c, i) => string(c, `${path}.selection.rows.columns[${i}]`, 1, 256));
    if (new Set(names).size !== names.length) fail(`${path}.selection.rows.columns`, 'columns must be unique');
    selection = Object.freeze({rows: Object.freeze({offset: integer(rows['offset'], `${path}.selection.rows.offset`, 0), limit: integer(rows['limit'], `${path}.selection.rows.limit`, 1, LIMITS.rows), columns: Object.freeze(names)})});
  } else if (kind === 'figure') {
    if (keys.join() !== 'figure') fail(`${path}.selection`, 'a figure selects a static view size');
    const figure = object((raw as Record<string, unknown>)['figure'], `${path}.selection.figure`, ['width', 'height']);
    selection = Object.freeze({figure: Object.freeze({
      width: integer(figure['width'], `${path}.selection.figure.width`, LIMITS.figureWidth[0], LIMITS.figureWidth[1]),
      height: integer(figure['height'], `${path}.selection.figure.height`, LIMITS.figureHeight[0], LIMITS.figureHeight[1]),
    })});
  } else {
    if (keys.length) fail(`${path}.selection`, `a ${kind} output takes no selection`);
    selection = Object.freeze({});
  }
  return Object.freeze({
    id: uuid(record['id'], `${path}.id`),
    execution_id: uuid(record['execution_id'], `${path}.execution_id`),
    output_id: hex(record['output_id'], `${path}.output_id`, HEX64, '64-hex output id'),
    content_hash: hex(record['content_hash'], `${path}.content_hash`, HEX64, '64-hex content hash'),
    kind: kind as EvidenceKind,
    selection,
    labels: labels(record['labels'], `${path}.labels`),
  });
};
/** The validated, deeply frozen manifest; throws `PresentationError` naming the failing path. */
export function validatePresentation(value: unknown): PresentationManifest {
  const record = object(value, '', ['schema', 'name', 'notes', 'recording', 'layout', 'camera', 'visibility', 'measurements', 'time', 'rate', 'annotations', 'evidence', 'created'], ['id', 'revision']);
  if (record['schema'] !== 1) fail('schema', 'must be 1');
  const recording = object(record['recording'], 'recording', ['attempt_id', 'request_id', 'trace_sha256']);
  const layout = ((): LayoutBundle => {
    try {
      const bundle = layoutOf(record['layout']);
      if (bundle.schema_version !== '1.0') throw new TypeError('schema_version must be "1.0"');
      return bundle;
    } catch (error) {return fail('layout', error instanceof Error ? error.message : String(error));}
  })();
  const camera = object(record['camera'], 'camera', ['bounds']);
  const bounds = ((): [number, number, number, number] => {
    const raw = camera['bounds'];
    if (Array.isArray(raw) && raw.length === 4 && raw.every(v => typeof v === 'number' && Number.isFinite(v)) && raw[0] < raw[2] && raw[1] < raw[3])
      return [raw[0], raw[1], raw[2], raw[3]];
    return fail('camera.bounds', 'must be four finite numbers [xmin, ymin, xmax, ymax] with xmin < xmax and ymin < ymax');
  })();
  const visibility = object(record['visibility'], 'visibility', ['entities', 'labels']);
  const measurements = list(record['measurements'], 'measurements', LIMITS.measurements, 'measurement keys').map((m, i) => string(m, `measurements[${i}]`, 1, 1024));
  if (new Set(measurements).size !== measurements.length) fail('measurements', 'keys must be unique');
  const time = object(record['time'], 'time', ['start', 'end']);
  const start = number(time['start'], 'time.start'), end = number(time['end'], 'time.end');
  if (!(start < end)) fail('time', 'start must be before end');
  const rate = number(record['rate'], 'rate');
  if (!(rate > 0) || rate > LIMITS.rate) fail('rate', `must be above 0 and at most ${LIMITS.rate}`);
  const annotations = list(record['annotations'], 'annotations', LIMITS.annotations, 'annotations').map((a, i) => {
    const path = `annotations[${i}]`;
    const item = object(a, path, ['id', 'text', 'time']);
    return Object.freeze({id: uuid(item['id'], `${path}.id`), text: string(item['text'], `${path}.text`, 1, LIMITS.annotationText), time: item['time'] === null ? null : number(item['time'], `${path}.time`)});
  });
  const evidenceList = list(record['evidence'], 'evidence', LIMITS.evidence, 'evidence selections').map((e, i) => evidence(e, `evidence[${i}]`));
  if (new Set(evidenceList.map(e => e.id)).size !== evidenceList.length) fail('evidence', 'ids must be unique');
  const manifest: PresentationManifest = {
    schema: 1,
    ...('id' in record ? {id: uuid(record['id'], 'id')} : {}),
    ...('revision' in record ? {revision: integer(record['revision'], 'revision', 0)} : {}),
    name: string(record['name'], 'name', 1, LIMITS.name),
    notes: string(record['notes'], 'notes', 0, LIMITS.notes),
    recording: Object.freeze({
      attempt_id: hex(recording['attempt_id'], 'recording.attempt_id', HEX32, '32-hex attempt id'),
      request_id: hex(recording['request_id'], 'recording.request_id', HEX64, '64-hex request id'),
      trace_sha256: hex(recording['trace_sha256'], 'recording.trace_sha256', HEX64, '64-hex trace hash'),
    }),
    layout,
    camera: Object.freeze({bounds: Object.freeze(bounds)}),
    visibility: Object.freeze({entities: idList(visibility['entities'], 'visibility.entities'), labels: idList(visibility['labels'], 'visibility.labels')}),
    measurements: Object.freeze(measurements),
    time: Object.freeze({start, end}),
    rate,
    annotations: Object.freeze(annotations),
    evidence: Object.freeze(evidenceList),
    created: number(record['created'], 'created'),
  };
  if (manifest.created < 0) fail('created', 'must be epoch seconds');
  return Object.freeze(manifest);
}
/** What `applyPresentation` and `capturePresentation` need from a scene, a session and a playback. */
export interface SceneView {
  fitBounds(bounds: Bounds): void;
  setVisibility(visibility: {entities: ReadonlySet<string> | null; labels: ReadonlySet<string> | null}): void;
  worldBounds(): Bounds;
}
export interface SessionView {setSeries(keys: readonly string[]): void;}
export interface PlaybackView {rate: number; seekTime(time: number): Promise<void>;}
/** Restore a saved view: framing, visibility and labels, measurements, rate and the start time. */
export async function applyPresentation(scene: SceneView, session: SessionView, playback: PlaybackView, manifest: PresentationManifest): Promise<void> {
  scene.fitBounds(manifest.camera.bounds);
  scene.setVisibility({
    entities: manifest.visibility.entities === null ? null : new Set(manifest.visibility.entities),
    labels: manifest.visibility.labels === null ? null : new Set(manifest.visibility.labels),
  });
  session.setSeries(manifest.measurements);
  playback.rate = manifest.rate;
  await playback.seekTime(manifest.time.start);
}
/** The current view as a manifest: the camera comes from the scene's visible world rectangle, never from the DOM. */
export function capturePresentation(scene: SceneView, _session: SessionView, playback: PlaybackView, selection: PresentationSelection): PresentationManifest {
  const bounds = scene.worldBounds();
  const {rate, created, ...rest} = selection;
  return validatePresentation({
    ...rest,
    schema: 1,
    camera: {bounds: [bounds[0], bounds[1], bounds[2], bounds[3]]},
    rate: rate ?? playback.rate,
    created: created ?? Date.now() / 1000,
  });
}
/** Keep playback inside a saved time range: seeks are clamped and playing pauses at the end. */
export function enforceRange(playback: {seekTime(time: number): Promise<void>; pause(): void; subscribe(listener: (frame: {time: number} | null, status: string) => void): () => void}, range: PlaybackRange): () => void {
  if (!(range.start < range.end) || ![range.start, range.end].every(Number.isFinite)) throw new RangeError('Invalid playback range');
  const original = playback.seekTime.bind(playback);
  let clamping = false;
  playback.seekTime = (time: number) => original(Math.min(range.end, Math.max(range.start, time)));
  const stop = playback.subscribe((frame, status) => {
    if (clamping || !frame) return;
    if (status === 'playing' && frame.time >= range.end) {
      clamping = true;
      playback.pause();
      void original(range.end).finally(() => {clamping = false;});
    }
  });
  return () => {
    stop();
    playback.seekTime = original;
  };
}
