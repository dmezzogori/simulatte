import {useState} from 'react';
import {asObject} from './layout-canvas-model';
import type {Layer} from './layout-history';
/** Provenance describes the last resolved bundle, not an unvalidated ghost draft. */
export function LayoutProvenance({bundle, layer}: {bundle: Layer; layer: Layer}) {
  const [diagnosticPage, setDiagnosticPage] = useState(0);
  const [filter, setFilter] = useState(''), [page, setPage] = useState(0);
  const leaves = (Array.isArray(bundle.leaves) ? bundle.leaves : []).map(asObject)
    .filter(leaf => String(Array.isArray(leaf.path) ? leaf.path.join('.') : '').includes(filter));
  const current = Math.min(page, Math.max(0, Math.ceil(leaves.length / 40) - 1));
  const diagnostics = (Array.isArray(bundle.diagnostics) ? bundle.diagnostics : []).map(asObject);
  const currentDiagnostic = Math.min(diagnosticPage, Math.max(0, Math.ceil(diagnostics.length / 40) - 1));
  const targets = asObject(bundle.targets);
  const generated = Object.keys(asObject(layer.placements)).filter(id => asObject(targets[id]).explicitly_named === false);
  return <details><summary>Resolved provenance and diagnostics</summary>
    <p>These values describe the latest resolved layout. Validate the draft to refresh presentation changes.</p>
    {generated.length > 0 && <p role="alert">Generated ids: {generated.join(', ')}. Presentation overrides may become orphaned; physical writes require stable entity names.</p>}
    {diagnostics.slice(currentDiagnostic * 40, (currentDiagnostic + 1) * 40).map((item, index) => <p key={index} role="alert">{String(item.severity)} · {String(item.code)} · {String(item.target ?? '')}: {String(item.message)}
      {Array.isArray(item.path) && <span> ({item.path.join('.')})</span>}
    </p>)}
    {diagnostics.length > 40 && <div>
      <button type="button" disabled={currentDiagnostic === 0} onClick={() => setDiagnosticPage(currentDiagnostic - 1)}>Previous diagnostics</button>
      <span>{currentDiagnostic * 40 + 1}–{Math.min(diagnostics.length, (currentDiagnostic + 1) * 40)} of {diagnostics.length}</span>
      <button type="button" disabled={(currentDiagnostic + 1) * 40 >= diagnostics.length} onClick={() => setDiagnosticPage(currentDiagnostic + 1)}>Next diagnostics</button>
    </div>}
    <p>For an orphan or kind mismatch, reassign its named placement or edit its override target, then validate. Reset deletes the file override; lane segment overrides stay attached to their original vertex ids.</p>
    <label>Find layout path<input value={filter} onChange={event => {setFilter(event.target.value); setPage(0);}} /></label>
    <table><thead><tr><th>Path</th><th>State</th><th>Source</th></tr></thead><tbody>
      {leaves.slice(current * 40, (current + 1) * 40).map((leaf, index) => <tr key={index}>
        <td>{Array.isArray(leaf.path) ? leaf.path.join('.') : ''}</td><td>{String(leaf.state)}</td>
        <td>{leaf.schema_default ? 'schema default' : String(asObject(leaf.source).kind ?? 'absent')}{asObject(leaf.source).stage ? ` · ${String(asObject(leaf.source).stage)}` : ''}</td>
      </tr>)}
    </tbody></table>
    <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous provenance</button>
    <span>{leaves.length ? current * 40 + 1 : 0}–{Math.min(leaves.length, (current + 1) * 40)} of {leaves.length}</span>
    <button type="button" disabled={(current + 1) * 40 >= leaves.length} onClick={() => setPage(current + 1)}>Next provenance</button>
  </details>;
}
