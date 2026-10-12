import {useState} from 'react';
import {Parameters} from './parameters';
import {LayoutEditor} from './layout-editor';
import type {ConnectionStatus, StudioClient, StudioState} from './studio-client';

/** Execution controls use authoritative revisions; disconnect never owns a run. */
export function StudioPanel({client, state, status, onError}: {
  client: StudioClient | null; state: StudioState | null; status: ConnectionStatus; onError(message: string): void;
}) {
  const [busy, setBusy] = useState(false);
  const act = async (cancel: boolean) => {
    if (!client || !state || busy) return;
    setBusy(true);
    try {
      const key = state.active?.key;
      const result = cancel && key
        ? await client.command('run.cancel', {request_id: key.request_id}, {revision: state.revision, key})
        : await client.command('run.start', {}, {revision: state.revision});
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      await client.resync();
    } catch (error) {onError(String(error));}
    finally {setBusy(false);}
  };
  const issues = Object.entries(state?.inputs?.issues ?? {});
  const connected = status === 'connected';
  return <section className="studio-controls" aria-label="Studio execution">
    <div><strong>Local Studio</strong> <span role="status">{status.replaceAll('_', ' ')}</span>
      {state?.revision && <span> · revision {state.revision.number}</span>}
      <span> · {state?.active?.status ?? state?.startup.phase ?? 'preparing'}</span>
    </div>
    <div className="row">
      <button disabled={!connected || busy || !state?.inputs || issues.length > 0 || !!state.fatal || state.startup.phase === "error"} onClick={() => void act(false)}>Run</button>
      <button disabled={!connected || busy || !state?.active} onClick={() => void act(true)}>Cancel run</button>
      <button disabled={!connected || busy} onClick={() => void client?.resync().catch(error => onError(String(error)))}>Refresh state</button>
    </div>
    {status === 'authentication_required' && <p>Enter link in the serving terminal and open the fresh browser link.</p>}
    {state?.startup.error != null && <pre role="alert">{JSON.stringify(state.startup.error, null, 2)}</pre>}
    {state?.fatal != null && <pre role="alert">{JSON.stringify(state.fatal, null, 2)}</pre>}
    {issues.length > 0 && <ul>{issues.map(([name, issue]) => <li key={name}>{name}: {String(issue)}</li>)}</ul>}
    <Parameters client={client} state={state} status={status} />
    <LayoutEditor client={client} state={state} status={status} />
  </section>;
}
