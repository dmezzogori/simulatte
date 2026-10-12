import type {StudioState} from './studio-client';

/** Indexed results are immutable; historical selection uses its recorded layout. */
export function RunHistory({state, select}: {state: StudioState | null;
  select?(result: StudioState['results'][number]): void}) {
  return <details><summary>Run history ({state?.results.length ?? 0})</summary>
    <p>The latest 20 committed runs with their recorded layout. Selection stays pinned until Follow live.</p>
    <ul>{state?.results.map(result => <li key={result.key.attempt_id}>
      <button disabled={!result.trace} onClick={() => select?.(result)}>
        Revision {result.revision} · {result.key.attempt_id.slice(0, 8)}
      </button>
    </li>)}</ul>
  </details>;
}
