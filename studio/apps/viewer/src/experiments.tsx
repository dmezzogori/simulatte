import {useEffect, useRef, useState, useSyncExternalStore} from 'react';
import {buildDefinition, defineExperiment, emptyForm, type ExperimentForm} from './experiment-form';
import {ExperimentCharts} from './experiment-charts';
import type {ExperimentFeed, FeedSnapshot} from './experiment-feed';
import {MAX_OVERLAY} from './overlay-charts';
import type {ParameterField, ParameterSchema} from './parameter-draft';
import {ResultsTable, type ResultsOptions} from './results-table';
import type {StudioPlayback} from './studio-playback';
import type {ConnectionStatus, ExperimentReport, ExperimentRun, ReplicationEntry, StudioClient, StudioState} from './studio-client';

interface Metadata {selected: string; models: {name: string; parameters: ParameterSchema}[];}
const committed = (item: ReplicationEntry) => (item.status === 'succeeded' || item.status === 'reused') && item.trace !== null;

/** Opens a committed replication in the normal viewer; returns false when it has no committed trace. */
export function openReplication(playback: Pick<StudioPlayback, 'select'> | null, report: Pick<ExperimentReport, 'name' | 'configurations'>, replication: ReplicationEntry): boolean {
  if (!playback || !committed(replication)) return false;
  const label = report.configurations[replication.configuration]?.label ?? String(replication.configuration);
  playback.select(replication.trace!, 0, null, `${report.name} · ${label} · replication ${replication.index}`);
  return true;
}
export const Counts = ({counts, onSelect}: {counts: Record<string, number>; onSelect?(status: string): void}) =>
  <span className="counts">{Object.entries(counts).filter(([, count]) => count > 0).map(([state, count]) => onSelect
    ? <button key={state} className="link" onClick={() => onSelect(state)}>{state} {count}</button>
    : <span key={state}>{state} {count}</span>)}</span>;

/** The experiment definition form; saves through `experiment.define` after client-side validation. */
export function DefinitionForm({client, fields, model, workers, onSaved, onError}: {client: StudioClient; fields: readonly ParameterField[]; model: string;
  workers: readonly string[]; onSaved(path: string): void; onError(message: string): void}) {
  const [form, setForm] = useState<ExperimentForm>(() => emptyForm(model));
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const {issues} = buildDefinition(form, fields);
  const change = (next: Partial<ExperimentForm>) => setForm({...form, ...next});
  const issue = (key: string) => issues[key] ? <small role="alert">{issues[key]}</small> : null;
  const save = async () => {
    setBusy(true);
    try {
      const result = await defineExperiment(client, form, fields, overwrite);
      if (result.ok) onSaved(result.path);
      else if (result.error !== undefined) onError(JSON.stringify(result.error));
    } catch (error) {onError(String(error));}
    finally {setBusy(false);}
  };
  const stopping = form.stopping;
  return <form className="parameter-form experiment-form" aria-label="New experiment" onSubmit={event => {event.preventDefault(); void save();}}>
    <fieldset disabled={busy}><legend>Definition</legend>
      <label>Name<input value={form.name} onChange={event => change({name: event.target.value,
        path: form.path === '' || form.path === `${form.name}.experiment.json` ? `${event.target.value}.experiment.json` : form.path})} />{issue('name')}</label>
      <label>File<input value={form.path} onChange={event => change({path: event.target.value})} />{issue('path')}</label>
      <label>Model<input value={form.model} onChange={event => change({model: event.target.value})} />{issue('model')}</label>
      <label>Replications<input inputMode="numeric" value={form.count} onChange={event => change({count: event.target.value})} />{issue('replications.count')}</label>
      <label>Base seed<input inputMode="numeric" value={form.base_seed} onChange={event => change({base_seed: event.target.value})} />{issue('replications.base_seed')}</label>
    </fieldset>
    <fieldset disabled={busy}><legend>Configurations</legend>
      <label><input type="radio" checked={form.mode === 'grid'} onChange={() => change({mode: 'grid'})} /> Grid (Cartesian product)</label>
      <label><input type="radio" checked={form.mode === 'explicit'} onChange={() => change({mode: 'explicit'})} /> Explicit list</label>
      {form.mode === 'grid' ? fields.map(field => <label key={field.name}>{field.name} <small>{field.type}{field.required ? ' · required' : ` · default ${String(field.default)}`} · comma-separated</small>
        <input value={form.grid[field.name] ?? ''} aria-invalid={!!issues[`grid.${field.name}`]}
          onChange={event => change({grid: {...form.grid, [field.name]: event.target.value}})} />{issue(`grid.${field.name}`)}</label>)
        : <>{form.explicit.map((row, index) => <div key={index} className="explicit-row">
          <label>Label<input value={row.label} onChange={event => change({explicit: form.explicit.map((item, other) => other === index ? {...item, label: event.target.value} : item)})} /></label>
          {fields.map(field => <label key={field.name}>{field.name}<input value={row.values[field.name] ?? ''} placeholder={field.default === null ? '' : String(field.default)}
            onChange={event => change({explicit: form.explicit.map((item, other) => other === index ? {...item, values: {...item.values, [field.name]: event.target.value}} : item)})} /></label>)}
          <button type="button" disabled={form.explicit.length < 2} title={form.explicit.length < 2 ? 'Keep at least one configuration' : undefined} onClick={() => change({explicit: form.explicit.filter((_, other) => other !== index)})}>Remove</button>
          {issue(`explicit.${index}`)}
        </div>)}
        <button type="button" onClick={() => change({explicit: [...form.explicit, {label: '', values: {}}]})}>Add configuration</button></>}
      {issue('configurations')}
    </fieldset>
    <fieldset disabled={busy}><legend>Execution</legend>
      <label>Layout variants (label=file per line; empty uses the project layout)<textarea value={form.layouts} onChange={event => change({layouts: event.target.value})} />{issue('layouts')}</label>
      <label>Stopping policy<select value={stopping?.type ?? ''} onChange={event => change({stopping: event.target.value === '' ? null
        : event.target.value === 'horizon' ? {type: 'horizon', horizon: '100', warmup: '0'} : {type: 'finite_population', arrivals: '1', cutoff: '', safety_limit: '100'}})}>
        <option value="">Model default</option><option value="horizon">Horizon</option><option value="finite_population">Finite population</option>
      </select></label>
      {stopping && (stopping.type === 'horizon' ? ['horizon', 'warmup'] : ['arrivals', 'cutoff', 'safety_limit']).map(name =>
        <label key={name}>{name.replaceAll('_', ' ')}<input inputMode="decimal" value={stopping[name] ?? ''}
          onChange={event => change({stopping: {...stopping, [name]: event.target.value}})} /></label>)}
      {issue('stopping')}
      <label>Recording<select value={form.level} onChange={event => change({level: event.target.value as ExperimentForm['level']})}>
        <option value="kpi">KPIs only</option><option value="full">Full trace</option>
      </select></label>
      <label>KPI keys (one per line; empty records all)<textarea value={form.kpis} onChange={event => change({kpis: event.target.value})} />{issue('kpis')}</label>
      <div>Workers (none selected: local only)
        {['local', ...workers].map(name => <label key={name}><input type="checkbox" checked={form.workers.includes(name)}
          onChange={event => change({workers: event.target.checked ? [...form.workers, name] : form.workers.filter(item => item !== name)})} /> {name}</label>)}
        {issue('workers')}
      </div>
    </fieldset>
    <label><input type="checkbox" checked={overwrite} onChange={event => setOverwrite(event.target.checked)} /> Replace an existing file</label>
    <button type="submit" disabled={busy || Object.keys(issues).length > 0} title={busy ? 'Saving…' : Object.keys(issues).length ? `${Object.keys(issues).length} issue(s) to fix before saving` : undefined}>Save</button>
    <span role="status">{busy ? 'Saving…' : Object.keys(issues).length ? `${Object.keys(issues).length} issue(s)` : 'Valid; the server checks it against the model on save'}</span>
  </form>;
}

/** One run's configurations, results table, replications and charts. */
export function RunDetails({client, run, counts, live, filter, onOpen, onError}: {client: StudioClient; run: ExperimentRun; counts: Record<string, number>;
  live: FeedSnapshot; filter: string | null; onOpen(report: ExperimentReport, replication: ReplicationEntry): void; onError(message: string): void}) {
  const [options, setOptions] = useState<ResultsOptions>({level: 0.95, bonferroni: false, baseline: null});
  const [report, setReport] = useState<ExperimentReport | null>(null);
  const [status, setStatus] = useState(filter);
  // A configuration filter lists that configuration's replications server-side, past the report's cap.
  const [configuration, setConfiguration] = useState<number | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const request = useRef(0);
  useEffect(() => setStatus(filter), [filter]);
  useEffect(() => {
    const id = ++request.current; setBusy(true);
    void client.command('comparison.get', {run_id: run.run_id, level: options.level, bonferroni: options.bonferroni,
      ...(options.baseline === null ? {} : {baseline: options.baseline}), ...(configuration === null ? {} : {configuration})}).then(result => {
      if (id !== request.current) return;
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      setReport(result.report as ExperimentReport);
    }).catch(error => {if (id === request.current) onError(String(error));}).finally(() => {if (id === request.current) setBusy(false);});
    return () => {request.current++;};
  }, [client, run.run_id, run.status, options.level, options.bonferroni, options.baseline, configuration, refresh]);
  if (!report) return <p>{busy ? 'Loading results…' : 'No results.'}</p>;
  const progress = live.progress(run.run_id)?.configurations;
  const replications = report.replications.filter(item => status === null || item.status === status);
  const chosen = report.replications.filter(item => item.attempt !== null && selected.includes(item.attempt));
  return <div className="run-details">
    <h4>{report.name} · {run.status} <button disabled={busy} title={busy ? 'Loading results…' : undefined} onClick={() => setRefresh(refresh + 1)}>Refresh results</button></h4>
    <table><thead><tr><th>Configuration</th><th>Progress</th></tr></thead>
      <tbody>{report.configurations.map(item => <tr key={item.index}><td>{item.label}</td><td><Counts counts={progress?.[item.index] ?? item.counts} /></td></tr>)}</tbody></table>
    <ResultsTable report={report} options={options} onOptions={setOptions} busy={busy} />
    <details open={status !== null}><summary>Replications ({report.replications_total}) · <Counts counts={counts} /></summary>
      <label>Status <select value={status ?? ''} onChange={event => setStatus(event.target.value || null)}>
        <option value="">All</option>{Object.keys(counts).map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      <label>Configuration <select value={configuration ?? ''} disabled={busy} onChange={event => setConfiguration(event.target.value === '' ? null : Number(event.target.value))}>
        <option value="">All</option>{report.configurations.map(item => <option key={item.index} value={item.index}>{item.label}</option>)}
      </select></label>
      <table><thead><tr><th>Overlay</th><th>Configuration</th><th>Replication</th><th>Status</th><th /></tr></thead>
        <tbody>{replications.map(item => <tr key={`${item.configuration}\u0000${item.index}`}>
          <td><input type="checkbox" aria-label="Overlay this replication" disabled={!committed(item) || !selected.includes(item.attempt!) && selected.length >= MAX_OVERLAY}
            checked={item.attempt !== null && selected.includes(item.attempt)}
            onChange={event => setSelected(event.target.checked ? [...selected, item.attempt!] : selected.filter(attempt => attempt !== item.attempt))} /></td>
          <td>{report.configurations[item.configuration]?.label ?? item.configuration}</td><td>{item.index}</td><td>{item.status}</td>
          <td><button disabled={!committed(item)} title={committed(item) ? undefined : 'No committed recording for this replication'} onClick={() => onOpen(report, item)}>Open</button></td>
        </tr>)}</tbody></table>
      {report.replications.length < report.replications_total && <small>Showing the first {report.replications.length} of {report.replications_total} replications; choose a configuration to list its own.</small>}
    </details>
    <details><summary>Charts</summary><ExperimentCharts client={client} report={report} selected={chosen} /></details>
  </div>;
}

/** Experiments, their results and charts; progress re-renders at the feed's coalesced rate. */
export function Experiments({client, state, status, feed, onOpen, onError}: {client: StudioClient | null; state: StudioState | null; status: ConnectionStatus;
  feed: ExperimentFeed; onOpen(report: ExperimentReport, replication: ReplicationEntry): void; onError(message: string): void}) {
  const live = useSyncExternalStore(feed.subscribe, feed.snapshot);
  const [metadata, setMetadata] = useState<{id: string; value: Metadata} | null>(null);
  const [creating, setCreating] = useState(false);
  const [saved, setSaved] = useState<string[]>([]);
  const [file, setFile] = useState('');
  const [selection, setSelection] = useState<{run: string; filter: string | null} | null>(null);
  const [busy, setBusy] = useState(false);
  const reference = state?.metadata;
  useEffect(() => {
    if (!client || !reference) return;
    const abort = new AbortController();
    void client.reference(reference, abort.signal).then(value => {if (!abort.signal.aborted) setMetadata({id: reference.id, value: value as Metadata});})
      .catch(error => {if (!abort.signal.aborted) onError(String(error));});
    return () => abort.abort();
  }, [client, reference?.id]);
  const connected = status === 'connected' && !!client;
  const runs = state?.experiments ?? [];
  const files = [...new Set([...(state?.experiment_files ?? []), ...saved])].sort();
  const pool = live.workers ?? state?.workers ?? null;
  const model = metadata?.id === reference?.id ? metadata?.value.models.find(item => item.name === metadata.value.selected) : undefined;
  const act = async (type: 'experiment.start' | 'experiment.cancel' | 'experiment.resume' | 'experiment.delete', body: Record<string, unknown>) => {
    if (!client || busy) return;
    if (type === 'experiment.delete' && !window.confirm('Delete this experiment run and its replication results? This cannot be undone.')) return;
    setBusy(true);
    try {
      const result = await client.command(type, body);
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      if (type === 'experiment.start') setSelection({run: String(result.run_id), filter: null});
      if (type === 'experiment.delete' && selection?.run === body.run_id) setSelection(null);
      await client.resync();
    } catch (error) {onError(String(error));}
    finally {setBusy(false);}
  };
  const selected = runs.find(run => run.run_id === selection?.run);
  return <details className="studio-experiments"><summary>Experiments ({runs.length})</summary>
    <div className="row">
      <label>Experiment file <select value={file} onChange={event => setFile(event.target.value)}>
        <option value="">Choose a file</option>{files.map(name => <option key={name} value={name}>{name}</option>)}
      </select></label>
      <button disabled={!connected || busy || !file} onClick={() => void act('experiment.start', {path: file})}>Start</button>
      <button disabled={!model || !state?.model} onClick={() => setCreating(!creating)}>{creating ? 'Close form' : 'New experiment'}</button>
    </div>
    {creating && client && model && state?.model && <DefinitionForm client={client} fields={model.parameters.fields} model={`${state.model}:${metadata!.value.selected}`}
      workers={pool?.workers.map(worker => worker.name) ?? []} onError={onError}
      onSaved={path => {setSaved([...saved, path]); setFile(path); setCreating(false);}} />}
    {pool && <details><summary>Worker pool · {pool.local} local slot(s), {pool.workers.length} remote worker(s)</summary>
      {pool.error && <p role="alert">{pool.error}</p>}
      <ul>{pool.slots.map(slot => <li key={slot.name}>{slot.name} · <span className={`badge slot-${slot.state}`}>{slot.state}</span>
        {slot.last_code && <small> · {slot.last_code}</small>}</li>)}</ul>
    </details>}
    <table className="experiment-runs"><thead><tr><th>Name</th><th>Status</th><th>Replications</th><th>Created</th><th /></tr></thead>
      <tbody>{runs.map(run => {
        const counts = live.counts(run);
        return <tr key={run.run_id} aria-selected={run.run_id === selection?.run}>
          <td><button className="link" onClick={() => setSelection({run: run.run_id, filter: null})}>{run.name}</button></td>
          <td><span className={`badge run-${run.status}`}>{run.status}</span></td>
          <td><Counts counts={counts} onSelect={filter => setSelection({run: run.run_id, filter})} /></td>
          <td>{new Date(run.created * 1000).toLocaleString()}</td>
          <td>
            {(run.status === 'running' || run.status === 'paused') && <button disabled={!connected || busy} onClick={() => void act('experiment.cancel', {run_id: run.run_id})}>Cancel</button>}
            {run.status === 'paused' && <button disabled={!connected || busy} onClick={() => void act('experiment.resume', {run_id: run.run_id})}>Resume</button>}
            {run.status !== 'running' && run.status !== 'paused' && <button disabled={!connected || busy} onClick={() => void act('experiment.start', {run_id: run.run_id})}>Start again</button>}
            {run.status !== 'running' && <button disabled={!connected || busy} onClick={() => void act('experiment.delete', {run_id: run.run_id})}>Delete</button>}
          </td>
        </tr>;
      })}</tbody></table>
    {runs.length === 0 && <p>No experiment runs yet. Start one from a file or define a new one.</p>}
    {client && selected && <RunDetails key={selected.run_id} client={client} run={selected} counts={live.counts(selected)} live={live}
      filter={selection?.filter ?? null} onOpen={onOpen} onError={onError} />}
  </details>;
}
