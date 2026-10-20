import {afterEach, describe, expect, it, vi} from 'vitest';
import {buildDefinition, defineExperiment, emptyForm, type ExperimentForm} from '../src/experiment-form';
import {ExperimentFeed} from '../src/experiment-feed';
import {openReplication} from '../src/experiments';
import type {ParameterField} from '../src/parameter-draft';
import type {StudioPlayback} from '../src/studio-playback';
import type {ExperimentReport, Notification, StudioClient} from '../src/studio-client';

const field = (name: string, changes: Partial<ParameterField> = {}): ParameterField =>
  ({name, type: 'int', required: false, default: 1, choices: [], range: null, length: null, description: '', ...changes});
const fields = [field('servers', {range: {min: 1, max: 9}}), field('wip_norm'), field('rule', {type: 'enum', choices: ['FIFO', 'EDD'], default: 'FIFO'})];
// Design 4.1, verbatim.
const EXAMPLE = {
  schema: 1, name: 'servers-sweep', model: 'plant.py:build',
  replications: {count: 10, base_seed: 0},
  configurations: {grid: {servers: [2, 3, 4], wip_norm: [10, 20]}},
  layouts: [{label: 'base', file: 'layout.json'}, {label: 'wide', file: 'layouts/wide.json'}],
  stopping: null, level: 'kpi', kpis: [], workers: ['local', 'strangelove'],
};
const example = (): ExperimentForm => ({
  ...emptyForm('plant.py:build'), path: 'servers-sweep.experiment.json', name: 'servers-sweep', count: '10', base_seed: '0',
  grid: {servers: '2, 3, 4', wip_norm: '10,20'}, layouts: 'base=layout.json\nwide = layouts/wide.json\n', workers: ['local', 'strangelove'],
});

describe('experiment form', () => {
  it('builds the exact experiment-v1 dict of design 4.1', () => {
    const {definition, issues} = buildDefinition(example(), fields);
    expect(issues).toEqual({});
    expect(JSON.stringify(definition)).toBe(JSON.stringify(EXAMPLE));
  });
  it('requires schema fields and validates grid values per parameter type', () => {
    const issues = (changes: Partial<ExperimentForm>, schema = fields) => buildDefinition({...example(), ...changes}, schema).issues;
    expect(issues({name: ''})).toHaveProperty('name');
    expect(issues({model: 'plant'})).toHaveProperty('model');
    expect(issues({path: 'sweep.json'})).toHaveProperty('path');
    expect(issues({grid: {servers: '2, x'}})['grid.servers']).toMatch(/integer/);
    expect(issues({grid: {servers: '2, 2'}})['grid.servers']).toMatch(/repeat/);
    expect(issues({grid: {servers: '0'}})['grid.servers']).toMatch(/range/);
    expect(issues({grid: {rule: 'FIFO, LIFO'}})['grid.rule']).toMatch(/choice/);
    expect(issues({grid: {}}, [field('n', {required: true, default: null})])['grid.n']).toMatch(/Required/);
    expect(issues({layouts: 'bad label=layout.json'})).toHaveProperty('layouts');
    expect(issues({kpis: 'a\na'})).toHaveProperty('kpis');
    expect(issues({stopping: {type: 'horizon', horizon: '2', warmup: '2'}})).toHaveProperty('stopping');
    expect(buildDefinition({...example(), stopping: {type: 'horizon', horizon: '10', warmup: '2'}}, fields).definition?.stopping)
      .toEqual({type: 'horizon', horizon: 10, warmup: 2});
    const explicit = buildDefinition({...example(), mode: 'explicit', explicit: [{label: 'low', values: {servers: '2'}}, {label: '', values: {rule: 'EDD'}}]}, fields);
    expect(explicit.definition?.configurations).toEqual({explicit: [{label: 'low', parameters: {servers: 2}}, {parameters: {rule: 'EDD'}}]});
    expect(issues({mode: 'explicit', explicit: [{label: 'a/b', values: {}}]})).toHaveProperty('explicit.0');
  });
  it('enforces replication counts and seed bounds below 2**63', () => {
    const seed = (base_seed: string, count = '1') => buildDefinition({...example(), base_seed, count}, fields);
    for (const raw of ['-1', '01', '1e3', '1.5', '9223372036854775808']) expect(seed(raw).issues).toHaveProperty(['replications.base_seed']);
    expect(seed('9223372036854775807').definition?.replications).toEqual({count: 1, base_seed: '9223372036854775807'});
    expect(seed('9223372036854775807', '2').issues).toHaveProperty(['replications.base_seed']);
    expect(seed('9007199254740991').definition?.replications.base_seed).toBe(9007199254740991);
    expect(seed('9007199254740992').definition?.replications.base_seed).toBe('9007199254740992');
    expect(seed('', '3').definition?.replications).toEqual({count: 3, base_seed: 0});
    for (const count of ['0', '1000001', '2.5', '']) expect(seed('0', count).issues).toHaveProperty(['replications.count']);
  });
  it('validates before sending experiment.define', async () => {
    const command = vi.fn(async () => ({ok: true, path: 'servers-sweep.experiment.json', experiment_id: 'e'.repeat(64)}));
    const client = {command} as unknown as StudioClient;
    const rejected = await defineExperiment(client, {...example(), grid: {servers: 'x'}}, fields);
    expect(rejected.ok).toBe(false); expect(command).not.toHaveBeenCalled();
    const saved = await defineExperiment(client, example(), fields, true);
    expect(saved.ok).toBe(true);
    expect(command).toHaveBeenCalledExactlyOnceWith('experiment.define', {path: 'servers-sweep.experiment.json', definition: EXAMPLE, overwrite: true, measurements: null});
  });
  it('sends a selected measurement plan next to the definition, never inside it', async () => {
    const command = vi.fn(async () => ({ok: true, path: 'servers-sweep.experiment.json', experiment_id: 'e'.repeat(64)}));
    const client = {command} as unknown as StudioClient;
    const measured = {...example(), measurements: 'numbers'};
    expect(JSON.stringify(buildDefinition(measured, fields).definition)).toBe(JSON.stringify(EXAMPLE));
    await defineExperiment(client, measured, fields);
    expect(command).toHaveBeenCalledExactlyOnceWith('experiment.define', {path: 'servers-sweep.experiment.json', definition: EXAMPLE, overwrite: false, measurements: {plan: 'numbers'}});
    expect(emptyForm('plant.py:build').measurements).toBe('');
  });
});

describe('experiment progress', () => {
  afterEach(() => vi.useRealTimers());
  const progress = (seq: number, run: string, succeeded: number): Notification => ({v: 1, type: 'experiment_progress', event_seq: seq,
    body: {run_id: run, status: 'running', counts: {pending: 10 - succeeded, succeeded}, configurations: [{succeeded}]}});
  it('keeps the newest counts per run and publishes a snapshot at most once per 250 ms', () => {
    vi.useFakeTimers();
    const feed = new ExperimentFeed(); const listener = vi.fn(); feed.subscribe(listener);
    const listed = {run_id: 'r1', counts: {pending: 10, succeeded: 0}};
    const before = feed.snapshot();
    for (let i = 1; i <= 10; i++) feed.notification(progress(i, 'r1', i));
    feed.notification(progress(11, 'r2', 3));
    // No React-visible change per notification: the snapshot and its counts stay until the flush.
    expect(listener).not.toHaveBeenCalled();
    expect(feed.snapshot()).toBe(before); expect(feed.snapshot().counts(listed)).toEqual(listed.counts);
    vi.advanceTimersByTime(250);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(feed.snapshot().counts(listed)).toEqual({pending: 0, succeeded: 10});
    expect(feed.snapshot().progress('r2')?.configurations).toEqual([{succeeded: 3}]);
    feed.notification(progress(12, 'r1', 9));
    vi.advanceTimersByTime(249); expect(listener).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1); expect(listener).toHaveBeenCalledTimes(2);
    expect(feed.snapshot().counts(listed)).toEqual({pending: 1, succeeded: 9});
    expect(feed.snapshot().counts({run_id: 'r3', counts: {succeeded: 1}})).toEqual({succeeded: 1});
    // After a reconnect event_seq restarts; arrival order still applies the newest counts.
    feed.notification(progress(1, 'r1', 10));
    vi.advanceTimersByTime(250);
    expect(feed.snapshot().counts(listed)).toEqual({pending: 0, succeeded: 10});
    feed.dispose();
  });
  it('prefers a state snapshot that arrives after progress, including for a re-created run', async () => {
    vi.useFakeTimers();
    let release: (value: unknown) => void = () => {};
    const resolve = vi.fn().mockImplementationOnce(() => new Promise(done => {release = done;}));
    const feed = new ExperimentFeed(resolve as never);
    const listed = {run_id: 'r1', counts: {pending: 10, succeeded: 0}};
    feed.notification(progress(1, 'r1', 5)); vi.advanceTimersByTime(250);
    expect(feed.snapshot().counts(listed)).toEqual({pending: 5, succeeded: 5});
    // Run ids are content-derived: a deleted and re-created run is listed afresh, and the state wins.
    const reference = {id: 'f'.repeat(64), url: '/api/references/' + 'f'.repeat(64), length: 1, sha256: 'f'.repeat(64)};
    feed.notification({v: 1, type: 'experiment_progress', event_seq: 2, body: {ref: reference}});
    feed.observe();
    expect(feed.snapshot().counts(listed)).toEqual(listed.counts);
    expect(feed.snapshot().progress('r1')).toBeUndefined();
    // A referenced body sent before the state but resolved after it is stale.
    release(progress(2, 'r1', 7).body); await vi.advanceTimersByTimeAsync(250);
    expect(feed.snapshot().counts(listed)).toEqual(listed.counts);
    feed.notification(progress(3, 'r1', 1)); vi.advanceTimersByTime(250);
    expect(feed.snapshot().counts(listed)).toEqual({pending: 9, succeeded: 1});
    feed.notification({v: 1, type: 'workers_state', event_seq: 4, body: {local: 1, workers: [], slots: [], error: null}}); vi.advanceTimersByTime(250);
    expect(feed.snapshot().workers?.local).toBe(1);
    feed.observe();
    expect(feed.snapshot().workers).toBeNull();
    feed.dispose();
  });
  it('drops an older referenced progress body that resolves after a newer one', async () => {
    let release: (value: unknown) => void = () => {};
    const resolve = vi.fn().mockImplementationOnce(() => new Promise(done => {release = done;}));
    const feed = new ExperimentFeed(resolve as never, 0);
    const reference = {id: 'f'.repeat(64), url: '/api/references/' + 'f'.repeat(64), length: 1, sha256: 'f'.repeat(64)};
    feed.notification({v: 1, type: 'experiment_progress', event_seq: 1, body: {ref: reference}});
    feed.notification(progress(2, 'r1', 5));
    release(progress(1, 'r1', 1).body); await new Promise(done => setTimeout(done, 5));
    expect(feed.snapshot().counts({run_id: 'r1', counts: {}})).toEqual({pending: 5, succeeded: 5});
    feed.dispose();
  });
});

describe('replication selection', () => {
  it('opens a committed replication in the normal viewer', () => {
    const trace = {attempt: 'c'.repeat(32), generation: 3, length: 100, sha256: 'd'.repeat(64), url: `/api/prefix/${'c'.repeat(32)}/3`};
    const report = {name: 'sweep', configurations: [{index: 0, label: 'n=3/base', parameters: {}, counts: {}, mixed_runtime: false}]} as unknown as ExperimentReport;
    const select = vi.fn();
    const playback = {select} as unknown as StudioPlayback;
    expect(openReplication(playback, report, {configuration: 0, index: 1, status: 'succeeded', attempt: trace.attempt, trace})).toBe(true);
    expect(select).toHaveBeenCalledExactlyOnceWith(trace, 0, null, 'sweep · n=3/base · replication 1');
    expect(openReplication(playback, report, {configuration: 0, index: 2, status: 'failed', attempt: null, trace: null})).toBe(false);
    expect(select).toHaveBeenCalledTimes(1);
  });
});

it('keeps progress newer than a delayed state listing using transport receipt order', () => {
  const feed = new ExperimentFeed();
  feed.notification({v:1,type:'experiment_progress',event_seq:2,arrival:20,body:{run_id:'r1',status:'running',counts:{succeeded:7},configurations:[]}});
  feed.notification({v:1,type:'workers_state',event_seq:3,arrival:21,body:{local:2,workers:[],slots:[],error:null}});
  feed.observe(19);
  expect(feed.snapshot().progress('r1')?.counts.succeeded).toBe(7);
  expect(feed.snapshot().workers?.local).toBe(2);
  feed.observe(22);
  expect(feed.snapshot().progress('r1')).toBeUndefined();
  expect(feed.snapshot().workers).toBeNull();
  feed.dispose();
});
