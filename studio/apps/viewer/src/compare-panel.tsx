/** Compare: two committed full recordings side by side on one clock (design 7, D100). React keeps
 * the selections; frames update the time outputs, KPI tiles and transport imperatively. */
import {useEffect, useRef, useState, type RefObject} from 'react';
import {ComparePlayback, CompareRefused, type CompareRefusal, type Side} from './compare-playback';
import type {ConnectionStatus, ExperimentReport, Notification, Prefix, StudioClient, StudioState} from './studio-client';

/** A committed recording either side can open. */
export interface CompareEntry {
  readonly id: string;
  readonly kind: 'result' | 'replication' | 'replay';
  readonly label: string;
  /** The request `replay.start` re-executes at full level; null when unknown. */
  readonly request_id: string | null;
  readonly trace: Prefix;
  /** Replays only: the verification outcome and the first differing field. */
  readonly verified?: boolean | null;
  readonly reason?: string | null;
}
export type SideState =
  | {readonly phase: 'empty'}
  | {readonly phase: 'loading' | 'error'; readonly entry: CompareEntry; readonly message?: string}
  | {readonly phase: 'ready'; readonly entry: CompareEntry; readonly level: string}
  | {readonly phase: 'refused'; readonly entry: CompareEntry; readonly reason: CompareRefusal}
  | {readonly phase: 'replaying'; readonly entry: CompareEntry; readonly replay: string; readonly status: string;
      readonly verified: boolean | null; readonly reason: string | null};
export interface ReplayState {request_id: string; original: string; status: string; verified: boolean | null; message?: string;}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

/** `replay_state` notifications for the Compare panel; the app routes every notification here. */
export class ReplayNotifications {
  readonly #listeners = new Set<(state: ReplayState) => void>();
  notification(message: Notification): void {
    if (message.type !== 'replay_state' || !object(message.body) || typeof message.body.request_id !== 'string') return;
    for (const listener of [...this.#listeners]) listener(message.body as unknown as ReplayState);
  }
  subscribe(listener: (state: ReplayState) => void): () => void {
    this.#listeners.add(listener);
    return () => {this.#listeners.delete(listener);};
  }
}

/** Interactive results, the committed replications of one experiment report and succeeded replays.
 * An unverified replay is listed only when `includeUnverified` is on (D100); `hidden` counts the rest. */
export function compareEntries(state: Pick<StudioState, 'results' | 'replays'> | null, report: Pick<ExperimentReport, 'name' | 'configurations' | 'replications'> | null,
  includeUnverified: boolean): {entries: CompareEntry[]; hidden: number} {
  const entries: CompareEntry[] = [], labels = new Map<string, string>();
  for (const result of state?.results ?? []) {
    if (!result.trace) continue;
    const label = `Run · revision ${result.revision} · ${result.key.attempt_id.slice(0, 8)}`;
    labels.set(result.key.request_id, label);
    entries.push({id: result.key.attempt_id, kind: 'result', label, request_id: result.key.request_id, trace: result.trace});
  }
  for (const item of report?.replications ?? []) {
    if (!(item.status === 'succeeded' || item.status === 'reused') || !item.trace || !item.attempt) continue;
    const label = `${report!.name} · ${report!.configurations[item.configuration]?.label ?? item.configuration} · replication ${item.index}`;
    if (item.request_id) labels.set(item.request_id, label);
    entries.push({id: item.attempt, kind: 'replication', label, request_id: item.request_id ?? null, trace: item.trace});
  }
  let hidden = 0;
  for (const replay of state?.replays ?? []) {
    if (replay.status !== 'succeeded' || !replay.trace) continue;
    if (replay.verified !== true && !includeUnverified) {hidden++; continue;}
    entries.push({id: `replay:${replay.trace.attempt}`, kind: 'replay', label: `Replay of ${labels.get(replay.original) ?? replay.original.slice(0, 8)}`,
      request_id: replay.request_id, trace: replay.trace, verified: replay.verified, reason: replay.reason});
  }
  return {entries, hidden};
}

export function ReplayBadge({verified, reason}: {verified: boolean | null | undefined; reason: string | null | undefined}) {
  return verified ? <span className="badge replay-verified">verified replay</span>
    : <strong className="badge replay-unverified" title={reason ?? undefined}>unverified replay{reason ? `: ${reason}` : ''}</strong>;
}
const sideName = (index: Side) => index === 0 ? 'A' : 'B';

/** One side's status, scene host, time and KPI tiles. The host stays mounted (scenes attach to it)
 * and is hidden unless a full recording is loading or shown. */
export function CompareSideView({index, side, connected, onReplay, host, time, tiles}: {index: Side; side: SideState; connected: boolean; onReplay(): void;
  host: RefObject<HTMLDivElement | null>; time: RefObject<HTMLOutputElement | null>; tiles: RefObject<HTMLDivElement | null>}) {
  const name = sideName(index);
  const entry = side.phase === 'empty' ? null : side.entry;
  return <>
    {entry?.kind === 'replay' && <ReplayBadge verified={entry.verified} reason={entry.reason} />}
    {side.phase === 'loading' && <span role="status">Opening…</span>}
    {side.phase === 'ready' && <small> {side.level} trace</small>}
    {side.phase === 'error' && <span role="alert">{side.message}</span>}
    {side.phase === 'refused' && <p role="status">KPI-only recording: it has no entity events to play side by side.{' '}
      <button disabled={!connected || !side.entry.request_id} onClick={onReplay}>Replay at full level</button></p>}
    {side.phase === 'replaying' && (side.status === 'succeeded'
      ? side.verified ? <p role="status">Replay verified; opening it.</p>
        : <p role="status"><ReplayBadge verified={false} reason={side.reason} /> Unverified replays are excluded from side-by-side playback; turn on Include unverified replays to compare it.</p>
      : <p role="status">{side.status === 'starting' || side.status === 'running' || side.status === 'pending' ? 'Replay running at full level…' : `Replay ${side.status}`}</p>)}
    <div className="compare-scene" data-side={name} ref={host} hidden={side.phase !== 'ready' && side.phase !== 'loading'} />
    <output ref={time} aria-label={`Side ${name} time`}>t = —</output>
    <div className="tiles" ref={tiles} />
  </>;
}

const number = (value: number) => Number.isFinite(value) ? value.toLocaleString('en', {maximumFractionDigits: 4}) : value > 0 ? '+∞' : '−∞';
const EMPTY: SideState = {phase: 'empty'};

function CompareView({client, state, connected, headers, replays, onError, onClose}: {client: StudioClient | null; state: StudioState | null; connected: boolean;
  headers: Readonly<Record<string, string>>; replays: ReplayNotifications; onError(message: string): void; onClose(): void}) {
  const compare = useRef<ComparePlayback | null>(null);
  const hosts = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)] as const;
  const times = [useRef<HTMLOutputElement>(null), useRef<HTMLOutputElement>(null)] as const;
  const tiles = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)] as const;
  const tileValues = useRef<[Map<string, HTMLElement>, Map<string, HTMLElement>]>([new Map(), new Map()]);
  const slider = useRef<HTMLInputElement>(null), shared = useRef<HTMLOutputElement>(null), play = useRef<HTMLButtonElement>(null);
  const requests = useRef<[number, number]>([0, 0]);
  const [sides, setSides] = useState<readonly [SideState, SideState]>([EMPTY, EMPTY]);
  const [includeUnverified, setIncludeUnverified] = useState(false);
  const [run, setRun] = useState('');
  const [report, setReport] = useState<ExperimentReport | null>(null);
  const selectedRun = state?.experiments?.find(item => item.run_id === run);
  const runProgress = JSON.stringify([selectedRun?.status, selectedRun?.counts]);
  const [levels, setLevels] = useState<Readonly<Record<string, string>>>({});
  const setSide = (index: Side, value: SideState) => setSides(current => index === 0 ? [value, current[1]] : [current[0], value]);
  const transport = () => {
    const c = compare.current;
    if (!c) return;
    const time = c.time, range = c.range;
    if (slider.current) {
      slider.current.disabled = range === null;
      slider.current.min = String(range?.[0] ?? 0); slider.current.max = String(range?.[1] ?? 1);
      if (time !== null) slider.current.value = String(time);
    }
    if (shared.current) shared.current.textContent = time === null ? 't = —' : `t = ${number(time)}`;
    const label = c.playing ? 'Pause both' : 'Play both';
    // Keep the pressed text node across frames so WebKit delivers the click.
    if (play.current && play.current.textContent !== label) play.current.textContent = label;
  };
  useEffect(() => {
    const c = new ComparePlayback(headers);
    compare.current = c;
    const stop = c.subscribe((index, frame, _status, error) => {
      const output = times[index].current;
      if (frame && output) {output.textContent = `t = ${number(frame.time)}`; output.dataset['time'] = String(frame.time);}
      if (frame) for (const [key, node] of tileValues.current[index]) {
        const value = frame.kpis[key]?.value;
        node.textContent = value === null || value === undefined ? '—' : number(value);
      }
      transport();
      if (error) onError(String(error));
    });
    const hidden = () => c.setHidden(document.hidden);
    document.addEventListener('visibilitychange', hidden);
    // A native listener: frames set the slider's value imperatively, which React's change tracking would mask.
    const input = slider.current, seek = () => {if (input) void c.seekTime(Number(input.value));};
    input?.addEventListener('input', seek);
    return () => {
      input?.removeEventListener('input', seek);
      document.removeEventListener('visibilitychange', hidden);
      stop(); c.dispose(); compare.current = null;
    };
  }, []);
  useEffect(() => {
    if (!client || !run) {setReport(null); return;}
    let current = true;
    void client.command('comparison.get', {run_id: run}).then(result => {
      if (!current) return;
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      setReport(result.report as ExperimentReport);
    }).catch(error => {if (current) onError(String(error));});
    return () => {current = false;};
  }, [client, run, runProgress]);
  const clear = (index: Side) => {
    requests.current[index]++;
    compare.current?.close(index);
    tileValues.current[index].clear();
    tiles[index].current?.replaceChildren();
    const output = times[index].current;
    if (output) {output.textContent = 't = —'; delete output.dataset['time'];}
    transport();
  };
  const pick = (index: Side, entry: CompareEntry | null) => {
    clear(index);
    const c = compare.current;
    if (!entry || !c) {setSide(index, EMPTY); return;}
    const request = ++requests.current[index];
    setSide(index, {phase: 'loading', entry});
    void c.load(index, entry.trace, hosts[index].current).then(side => {
      if (request !== requests.current[index]) return;
      const values = tileValues.current[index], box = tiles[index].current;
      values.clear();
      box?.replaceChildren(...side.recording.seriesKeys.slice(0, 8).map(key => {
        const tile = document.createElement('div'), label = document.createElement('small'), value = document.createElement('strong');
        tile.className = 'tile'; label.textContent = key; value.textContent = '—';
        tile.append(label, value); values.set(key, value);
        return tile;
      }));
      setLevels(current => ({...current, [entry.id]: side.recording.trace.level}));
      setSide(index, {phase: 'ready', entry, level: side.recording.trace.level});
      transport();
    }).catch((error: unknown) => {
      if (request !== requests.current[index]) return;
      if (error instanceof CompareRefused) {
        setLevels(current => ({...current, [entry.id]: error.reason.level}));
        setSide(index, {phase: 'refused', entry, reason: error.reason});
      } else if (!(error instanceof DOMException && error.name === 'AbortError')) setSide(index, {phase: 'error', entry, message: String(error)});
    });
  };
  const replayAt = async (index: Side) => {
    const side = sides[index];
    if (side.phase !== 'refused' || !client || !side.entry.request_id) return;
    try {
      const result = await client.command('replay.start', {request_id: side.entry.request_id});
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      setSide(index, {phase: 'replaying', entry: side.entry, replay: String(result.request_id), status: 'starting', verified: null, reason: null});
    } catch (error) {onError(String(error));}
  };
  // Follow replay_state: progress and failures before scheduling arrive only as notifications.
  useEffect(() => replays.subscribe(update => setSides(current => {
    const follow = (side: SideState): SideState => side.phase === 'replaying' && side.replay === update.request_id
      ? {...side, status: update.status, verified: update.verified, reason: update.message ?? side.reason} : side;
    const next = [follow(current[0]), follow(current[1])] as const;
    return next[0] === current[0] && next[1] === current[1] ? current : next;
  })), [replays]);
  const listed = compareEntries(state, report, includeUnverified);
  // A finished replay appears in the state's replays: open it when verified or explicitly included.
  useEffect(() => {
    sides.forEach((side, index) => {
      if (side.phase !== 'replaying') return;
      const row = state?.replays?.find(item => item.request_id === side.replay && item.status === 'succeeded' && item.trace);
      if (!row) return;
      const entry = listed.entries.find(item => item.kind === 'replay' && item.request_id === row.request_id);
      if (entry) pick(index as Side, entry);
      else if (side.status !== 'succeeded' || side.verified !== row.verified)
        setSide(index as Side, {...side, status: 'succeeded', verified: row.verified, reason: row.reason});
    });
  }, [state?.replays, includeUnverified, sides]);
  const toggleUnverified = (include: boolean) => {
    setIncludeUnverified(include);
    // Turning the opt-in off removes unverified replays from side-by-side playback.
    if (!include) sides.forEach((side, index) => {
      if (side.phase !== 'empty' && side.entry.kind === 'replay' && side.entry.verified !== true) pick(index as Side, null);
    });
  };
  const runs = state?.experiments ?? [];
  const option = (entry: CompareEntry) => {
    const level = entry.kind === 'replay' ? 'full' : levels[entry.id];
    return <option key={entry.id} value={entry.id}>{entry.label}{level ? ` · ${level === 'full' ? 'full' : 'KPI-only'}` : ''}{entry.kind === 'replay' ? ` · ${entry.verified ? 'verified' : 'unverified'}` : ''}</option>;
  };
  const groups: [CompareEntry['kind'], string][] = [['result', 'Interactive runs'], ['replication', 'Experiment replications'], ['replay', 'Replays']];
  return <div className="compare-view">
    <div className="row">
      <label>Experiment run <select value={run} onChange={event => setRun(event.target.value)}>
        <option value="">Interactive runs only</option>{runs.map(item => <option key={item.run_id} value={item.run_id}>{item.name} · {item.status}</option>)}
      </select></label>
      <label><input type="checkbox" checked={includeUnverified} onChange={event => toggleUnverified(event.target.checked)} /> Include unverified replays</label>
      {listed.hidden > 0 && <small>{listed.hidden} unverified replay(s) hidden</small>}
      <button onClick={onClose}>Close compare</button>
    </div>
    <div className="compare-sides">{([0, 1] as const).map(index => {
      const side = sides[index], selected = side.phase === 'empty' ? null : side.entry;
      const entries = selected && !listed.entries.some(entry => entry.id === selected.id) ? [selected, ...listed.entries] : listed.entries;
      return <section key={index} className="compare-side" aria-label={`Compare side ${sideName(index)}`}>
        <label>Side {sideName(index)} <select aria-label={`Side ${sideName(index)} recording`} value={selected?.id ?? ''}
          onChange={event => pick(index, entries.find(entry => entry.id === event.target.value) ?? null)}>
          <option value="">Choose a committed recording</option>
          {groups.map(([kind, label]) => entries.some(entry => entry.kind === kind) &&
            <optgroup key={kind} label={label}>{entries.filter(entry => entry.kind === kind).map(option)}</optgroup>)}
        </select></label>
        <CompareSideView index={index} side={side} connected={connected} onReplay={() => void replayAt(index)} host={hosts[index]} time={times[index]} tiles={tiles[index]} />
      </section>;
    })}</div>
    <div className="transport compare-transport">
      <button aria-label="Compare previous event" onClick={() => void compare.current?.step(-1)}>◀│</button>
      <button className="primary" ref={play} onClick={() => {const c = compare.current; if (c) void (c.playing ? c.pause() : c.play()).then(transport);}}>Play both</button>
      <button aria-label="Compare next event" onClick={() => void compare.current?.step(1)}>│▶</button>
      <select aria-label="Compare speed" defaultValue="1" onChange={event => {if (compare.current) compare.current.rate = Number(event.target.value);}}>
        {[0.1, 0.25, 0.5, 1, 2, 5, 10, 50, 100].map(rate => <option key={rate} value={rate}>{rate}×</option>)}
      </select>
      <output ref={shared} aria-label="Shared time">t = —</output>
      <input ref={slider} aria-label="Compare time" type="range" disabled min="0" max="1" step="any" />
    </div>
  </div>;
}

/** The Compare section of the Studio panel. Opening it creates two scenes in its own hosts and its own
 * clock; closing it disposes both and leaves the main viewer's scene, canvas and clock untouched. */
export function ComparePanel({client, state, status, headers, replays, onError}: {client: StudioClient | null; state: StudioState | null; status: ConnectionStatus;
  headers: Readonly<Record<string, string>>; replays: ReplayNotifications; onError(message: string): void}) {
  const [open, setOpen] = useState(false);
  return <details className="studio-compare" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Compare</summary>
    {open && <CompareView client={client} state={state} connected={status === 'connected' && !!client} headers={headers} replays={replays}
      onError={onError} onClose={() => setOpen(false)} />}
  </details>;
}
