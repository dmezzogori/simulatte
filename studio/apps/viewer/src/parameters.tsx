import {useEffect, useRef, useState} from 'react';
import {ParameterDraft, parseExecutionOptions, type ExecutionControls, type ParameterField, type ParameterSchema, type Scalar} from './parameter-draft';
import type {ConnectionStatus, Revision, StudioClient, StudioState} from './studio-client';

interface Model {name: string; parameters: ParameterSchema; stopping: Record<string, unknown>;}
interface Metadata {selected: string; models: Model[];}
interface FormBase {model: Model; options: Record<string, unknown>; revision: Revision;}
function controls(options: Record<string, unknown>): ExecutionControls {
  return {seed: String(options.seed ?? '0'), level: String(options.level ?? 'full'), kpis: (options.kpis ?? []) as string[],
    stopping: Object.fromEntries(Object.entries(options.stopping as Record<string, unknown>).map(([key, value]) => [key, value === null ? '' : String(value)]))};
}
function values(base: FormBase): Record<string, Scalar> {return base.options.parameters as Record<string, Scalar>;}
function Field({field, value, issue, onChange}: {field: ParameterField; value: string | undefined; issue: string | undefined; onChange(value: string): void}) {
  const choices = field.choices.length ? field.choices : field.type === 'bool' ? [true, false] : [];
  return <div className="parameter-field">
    <label><span>{field.name} <small>{field.type}{field.required ? ' · required' : ` · default ${String(field.default)}`}</small></span>
      {choices.length ? <select value={value ?? ''} onChange={event => onChange(event.target.value)} aria-invalid={!!issue}>
        {value === undefined && <option value="" disabled>Choose a value</option>}
        {choices.map(choice => <option key={String(choice)} value={String(choice)}>{String(choice)}</option>)}
      </select> : <input value={value ?? ''} onChange={event => onChange(event.target.value)} aria-invalid={!!issue}
        inputMode={field.type === 'int' ? 'numeric' : field.type === 'float' ? 'decimal' : 'text'} />}
    </label>
    {value === undefined && field.type === 'str' && <button type="button" onClick={() => onChange('')}>Use empty string for {field.name}</button>}
    {field.description && <small>{field.description}</small>}
    {field.range && <small>Range: {field.range.min ?? 'unbounded'} to {field.range.max ?? 'unbounded'}</small>}
    {field.length && <small>Length: {field.length.min} to {field.length.max ?? 'unbounded'} characters</small>}
    {issue && <small role="alert">{issue}</small>}
  </div>;
}

function ParameterForm({client, base, enabled}: {client: StudioClient; base: FormBase; enabled: boolean}) {
  const [draft] = useState(() => new ParameterDraft(base.model.parameters, values(base), base.revision));
  const [options, setOptions] = useState(() => controls(base.options));
  const [optionsDirty, setOptionsDirty] = useState(false);
  const [pending, setPending] = useState<FormBase | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [, redraw] = useState(0);
  const applying = useRef(false);
  useEffect(() => {
    if (applying.current || base.revision.number <= draft.revision.number) return;
    draft.observe(base.model.parameters, values(base), base.revision, optionsDirty);
    if (draft.conflict) setPending(base);
    else {setOptions(controls(base.options)); setOptionsDirty(false); setPending(null);}
    redraw(value => value + 1);
  }, [base, draft, busy]);
  const validation = draft.validate();
  let execution: ReturnType<typeof parseExecutionOptions> | null = null, executionError = '';
  try {execution = parseExecutionOptions(options);} catch (error) {executionError = String(error);}
  const changeOptions = (next: ExecutionControls) => {setOptions(next); setOptionsDirty(true); setError('');};
  const rebase = (keep: boolean) => {
    if (!pending) return;
    draft.rebase(keep ? 'keep' : 'reload');
    if (!keep || !optionsDirty) {setOptions(controls(pending.options)); setOptionsDirty(false);}
    setPending(null); setError(''); redraw(value => value + 1);
  };
  const apply = async () => {
    if (!execution || pending || busy || !enabled || Object.keys(validation.issues).length) return;
    applying.current = true; setBusy(true); setError('');
    try {
      const result = await client.command('params.set', {...execution, parameters: validation.values}, {revision: draft.revision});
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      draft.accept(result.revision as unknown as Revision); setOptionsDirty(false);
      await client.resync();
    } catch (error) {setError(String(error));}
    finally {applying.current = false; setBusy(false);}
  };
  return <form className="parameter-form" aria-label="Model parameters" onSubmit={event => {event.preventDefault(); void apply();}}>
    <h3>Parameters · {base.model.name}</h3>
    {pending && <div role="alert">Revision {pending.revision.number} has new inputs. Your draft is based on revision {draft.revision.number}.
      {JSON.stringify(pending.model.parameters) !== JSON.stringify(draft.schema) && <p>New schema: {pending.model.parameters.fields.map(field => `${field.name} (${field.type})`).join(', ')}</p>}
      <button type="button" disabled={busy} onClick={() => rebase(true)}>Keep edits on new revision</button>
      <button type="button" disabled={busy} onClick={() => rebase(false)}>Reload authoritative values</button>
    </div>}
    <fieldset disabled={busy || !enabled}>
      <legend>Model inputs</legend>
      {draft.schema.fields.map(field => <Field key={field.name} field={field} value={draft.raw(field.name)} issue={validation.issues[field.name]}
        onChange={raw => {draft.set(field.name, raw); setError(''); redraw(value => value + 1);}} />)}
      {draft.removed.map(name => <div key={name} role="alert">{name}: {validation.issues[name]}
        <button type="button" onClick={() => {draft.discard(name); redraw(value => value + 1);}}>Discard removed {name}</button></div>)}
    </fieldset>
    <fieldset disabled={busy || !enabled}>
      <legend>Execution</legend>
      <label>Seed<input inputMode="numeric" value={options.seed} onChange={event => changeOptions({...options, seed: event.target.value})} /></label>
      <label>Recording<select value={options.level} onChange={event => changeOptions({...options, level: event.target.value})}>
        <option value="full">Full trace</option><option value="kpi">KPIs only</option>
      </select></label>
      <label>Stopping policy<select value={options.stopping.type} onChange={event => changeOptions({...options,
        stopping: event.target.value === 'horizon' ? {type: 'horizon', horizon: '100', warmup: '0'} : {type: 'finite_population', arrivals: '1', cutoff: '', safety_limit: '100'}})}>
        <option value="horizon">Horizon</option><option value="finite_population">Finite population</option>
      </select></label>
      {(options.stopping.type === 'horizon' ? ['horizon', 'warmup'] : ['arrivals', 'cutoff', 'safety_limit']).map(name =>
        <label key={name}>{name.replaceAll('_', ' ')}<input inputMode="decimal" value={options.stopping[name] ?? ''}
          onChange={event => changeOptions({...options, stopping: {...options.stopping, [name]: event.target.value}})} /></label>)}
      <label>KPI keys (one per line; empty selects all)<textarea value={options.kpis.join('\n')}
        onChange={event => changeOptions({...options, kpis: event.target.value === '' ? [] : event.target.value.split('\n')})} /></label>
      {executionError && <p role="alert">{executionError}</p>}
    </fieldset>
    {error && <p role="alert">{error}</p>}
    <button type="submit" disabled={!enabled || busy || !!pending || !!executionError || Object.keys(validation.issues).length > 0}>Apply and run</button>
    <span role="status">{busy ? 'Applying…' : draft.dirty || optionsDirty ? 'Unsaved edits' : 'Inputs synchronized'}</span>
  </form>;
}

/** Metadata is read by content reference; a superseded response cannot replace a form. */
export function Parameters({client, state, status}: {client: StudioClient | null; state: StudioState | null; status: ConnectionStatus}) {
  const [loaded, setLoaded] = useState<{id: string; metadata: Metadata} | null>(null);
  const [error, setError] = useState('');
  const reference = state?.metadata;
  useEffect(() => {
    if (!client || !reference) return;
    const abort = new AbortController(); setError('');
    void client.reference(reference, abort.signal).then(value => {
      if (!abort.signal.aborted) setLoaded({id: reference.id, metadata: value as Metadata});
    }).catch(error => {if (!abort.signal.aborted) setError(String(error));});
    return () => abort.abort();
  }, [client, reference?.id]);
  const [base, setBase] = useState<FormBase | null>(null);
  useEffect(() => {
    if (!loaded || loaded.id !== reference?.id || !state?.revision || !state.inputs) return;
    const model = loaded.metadata.models.find(model => model.name === loaded.metadata.selected);
    if (model) setBase({model, options: state.inputs.options, revision: state.revision});
  }, [loaded, reference?.id, state]);
  return <details className="studio-parameters"><summary>Parameters</summary>
    {error && <p role="alert">{error}</p>}
    {client && base ? <ParameterForm client={client} base={base}
      enabled={status === 'connected' && base.revision.number === state?.revision?.number && loaded?.id === reference?.id} /> : <p>Parameters become available after model inspection.</p>}
  </details>;
}
