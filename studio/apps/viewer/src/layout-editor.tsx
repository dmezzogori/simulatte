import {useCallback, useEffect, useRef, useState} from 'react';
import {validateLayoutPatch} from '@simulatte/trace';
import {LayoutDraft, type Layer} from './layout-history';
import {LayoutProvenance} from './layout-provenance';
import {LayoutCanvas} from './layout-canvas';
import {SchemaField, layoutRule} from './layout-schema-form';
import type {ConnectionStatus, Reference, Revision, StudioClient, StudioState} from './studio-client';

interface LayoutDocument {valid: boolean; layer?: Layer; raw?: string; error?: string;}
interface Loaded {document: LayoutDocument; revision: Revision;}
export function LayoutEditor({client, state, status}: {client: StudioClient | null; state: StudioState | null; status: ConnectionStatus}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [draft, setDraft] = useState<LayoutDraft | null>(null);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState('');
  const [bundle, setBundle] = useState<Layer>({});
  const [busy, setBusy] = useState(false);
  const [, redraw] = useState(0);
  const invalidPaths = useRef(new Set<string>());
  const invalid = useCallback((path: string, invalid: boolean) => {
    const before = invalidPaths.current.has(path);
    if (before === invalid) return;
    if (invalid) invalidPaths.current.add(path); else invalidPaths.current.delete(path);
    redraw(value => value + 1);
  }, []);
  const applying = useRef(false);
  const revision = state?.revision;
  const view = state?.view ?? state?.last_good;
  useEffect(() => {
    if (!client || !view) return;
    const abort = new AbortController();
    void client.reference(view.layout, abort.signal).then(value => {if (!abort.signal.aborted) setBundle(value as Layer);})
      .catch(error => {if (!abort.signal.aborted) setError(String(error));});
    return () => abort.abort();
  }, [client, view?.layout.id]);
  useEffect(() => {
    if (!client || !revision || status !== 'connected' || applying.current || loaded?.revision.number === revision.number) return;
    const abort = new AbortController();
    void client.command('layout.get').then(async result => {
      if (!result.ok || !result.revision) throw new Error('Layout is not available');
      const document = await client.reference(result.layout as unknown as Reference, abort.signal) as LayoutDocument;
      if (!abort.signal.aborted) setLoaded({document, revision: result.revision as unknown as Revision});
    }).catch(error => {if (!abort.signal.aborted) setError(String(error));});
    return () => abort.abort();
  }, [client, revision?.number, status, busy]);
  useEffect(() => {
    if (!loaded?.document.valid || !loaded.document.layer || applying.current) return;
    if (!draft) {setDraft(new LayoutDraft(loaded.document.layer, loaded.revision)); setPreview('');}
    else if (loaded.revision.number > draft.revision.number) {
      draft.observe(loaded.document.layer, loaded.revision); redraw(value => value + 1); setPreview('');
    }
  }, [loaded, draft, busy]);
  let validation = '';
  if (draft) try {validateLayoutPatch(draft.layer);} catch (error) {validation = String(error);}
  const enabled = status === 'connected' && loaded?.revision.number === revision?.number;
  const act = async (action: 'save' | 'preview' | 'undo' | 'redo') => {
    if (!client || !draft || busy || !enabled) return;
    setBusy(true); setError(''); applying.current = true;
    try {
      if (action === 'undo') draft.undo();
      if (action === 'redo') draft.redo();
      const proposal = draft.proposal(); validateLayoutPatch(proposal.layer);
      if (action === 'preview') {
        const result = await client.validateLayout(proposal.layer, proposal.revision);
        if (!result.ok) throw new Error(JSON.stringify(result.error));
        if (result.layout) setBundle(await client.reference(result.layout as unknown as Reference) as Layer);
        setPreview(result.requires_rerun ? 'Physical preparation passed in a disposable process; saving starts a fresh run.' : 'Presentation validation passed; saving updates the view without rerunning.');
      } else {
        const result = await client.writeLayout(proposal.layer, proposal.revision);
        if (!result.ok) throw new Error(JSON.stringify(result.error));
        draft.accept(result.revision as unknown as Revision); setPreview('');
        await client.resync();
      }
    } catch (error) {setError(String(error));}
    finally {applying.current = false; setBusy(false); redraw(value => value + 1);}
  };
  return <details className="studio-layout"><summary>Layout</summary>
    <div className="layout-editor">
      <p>File overrides. Reset inherits the code or automatic value. Choose Remove optional value ($unset) to suppress an inherited optional value.</p>
      {loaded && !loaded.document.valid && <div role="alert">{loaded.document.error}<pre>{loaded.document.raw}</pre></div>}
      {draft?.conflict && <div role="alert">The layout base revision changed. Your draft is preserved.
        <button disabled={busy} onClick={() => {try {draft.rebase('keep'); setError('');} catch (error) {setError(String(error));} redraw(value => value + 1);}}>Reapply non-conflicting layout edits</button>
        <button disabled={busy} onClick={() => {draft.rebase('reload'); setError(''); redraw(value => value + 1);}}>Reload layout</button>
      </div>}
      {draft && <>
        <LayoutProvenance bundle={bundle} layer={draft.layer} />
        <LayoutCanvas layer={draft.layer} bundle={bundle} enabled={enabled && !busy && !draft.conflict} onError={setError} change={value => {
          try {draft.edit(value); setError(''); setPreview('');} catch (error) {setError(String(error));}
          redraw(value => value + 1);
        }} />
        <fieldset disabled={!enabled || busy}>
          <SchemaField rule={layoutRule} value={draft.layer} path="layout" invalid={invalid} change={value => {
            try {draft.edit(value as Layer); setError(''); setPreview('');} catch (error) {setError(String(error));}
            redraw(value => value + 1);
          }} />
        </fieldset>
        {(validation || invalidPaths.current.size > 0) && <p role="alert">{invalidPaths.current.size ? 'Complete the numeric fields before saving.' : validation}</p>}
        <div className="row">
          <button disabled={!enabled || busy || draft.conflict || !!validation || invalidPaths.current.size > 0} onClick={() => void act('preview')}>Validate layout</button>
          <button disabled={!enabled || busy || !draft.dirty || draft.conflict || !!validation || invalidPaths.current.size > 0} onClick={() => void act('save')}>Apply layout</button>
          <button disabled={!enabled || busy || !draft.canUndo} onClick={() => void act('undo')}>Undo layout</button>
          <button disabled={!enabled || busy || !draft.canRedo} onClick={() => void act('redo')}>Redo layout</button>
          <button disabled={busy || !draft.dirty} onClick={() => {draft.discard(); setError(''); setPreview(''); redraw(value => value + 1);}}>Discard layout draft</button>
        </div>
        <span role="status">{busy ? 'Validating or saving…' : draft.dirty ? 'Unsaved layout edits' : `Layout revision ${draft.revision.number}`}</span>
      </>}
      {preview && <p role="status">{preview}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}
