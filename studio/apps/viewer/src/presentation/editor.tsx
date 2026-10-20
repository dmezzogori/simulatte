/** The presentation editor: saved framing capture, visibility and label pickers, measurements,
 * time range, rate, annotations and a bounded evidence picker over saved analyses. Nothing reaches
 * the manifest until Save. */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  PRESENTATION_LIMITS as LIMITS,
  PresentationError,
  capturePresentation,
  validatePresentation,
  type Annotation,
  type Bounds,
  type EvidenceChoice,
  type EvidenceSelection,
  type PlaybackView,
  type PresentationManifest,
  type SceneView,
  type SessionView,
} from "@simulatte/playback";
import type { StudioClient } from "../studio-client";
import type { OutputDescriptor, OutputManifest } from "../artifacts/types";
import { untag } from "../analysis/codec";
import { FailureNotice } from "../workspace/feedback";
import { Actions, CheckField, Field, FieldGroup, NOT_CONNECTED, disabledWhen } from "../workspace/forms";
import { formatTime } from "../workspace/tables";
import { EvidenceItem, EvidenceQualifiers, analysisEvidenceSource, evidenceFromOutput, type EvidenceOrigin, type EvidenceSource } from "./evidence-panel";
import { PresentationStage, Transport, type StageHandle, type StageSource } from "./view";
import "./presentation.css";

export interface EditorDraft {
  name: string;
  notes: string;
  entities: string[] | null;
  labels: string[] | null;
  measurements: string[];
  time: { start: string; end: string };
  rate: number;
  annotations: Annotation[];
  evidence: EvidenceSelection[];
  /** The saved framing; `null` until "Use current view" or the first save takes the live view. */
  camera: Bounds | null;
}
export interface RecordingIdentity {
  attempt_id: string;
  request_id: string;
  trace_sha256: string;
}
export function draftOf(manifest: PresentationManifest | null): EditorDraft {
  return manifest
    ? {
        name: manifest.name,
        notes: manifest.notes,
        entities: manifest.visibility.entities ? [...manifest.visibility.entities] : null,
        labels: manifest.visibility.labels ? [...manifest.visibility.labels] : null,
        measurements: [...manifest.measurements],
        time: { start: String(manifest.time.start), end: String(manifest.time.end) },
        rate: manifest.rate,
        annotations: [...manifest.annotations],
        evidence: [...manifest.evidence],
        camera: manifest.camera.bounds,
      }
    : { name: "", notes: "", entities: null, labels: null, measurements: [], time: { start: "", end: "" }, rate: 1, annotations: [], evidence: [], camera: null };
}
/** The manifest a draft saves, with the camera taken from the stage when the draft has none. */
export function manifestOf(draft: EditorDraft, recording: RecordingIdentity, layout: unknown, camera: Bounds, existing: PresentationManifest | null): PresentationManifest {
  return validatePresentation({
    schema: 1,
    ...(existing?.id ? { id: existing.id, revision: existing.revision } : {}),
    name: draft.name,
    notes: draft.notes,
    recording,
    layout,
    camera: { bounds: camera },
    visibility: { entities: draft.entities, labels: draft.labels },
    measurements: draft.measurements,
    time: { start: Number(draft.time.start), end: Number(draft.time.end) },
    rate: draft.rate,
    annotations: draft.annotations,
    evidence: draft.evidence,
    created: existing?.created ?? Date.now() / 1000,
  });
}
/** "Use current view": the framing taken from the live stage, validated against the rest of the draft. The saved
 * rate stays the Rate field; the transport's playback rate is a temporary viewing change. */
export function currentView(
  draft: EditorDraft,
  context: { recording: RecordingIdentity; layout: unknown },
  scene: SceneView,
  session: SessionView,
  playback: PlaybackView,
): { camera: Bounds } {
  const captured = capturePresentation(scene, session, playback, {
    name: draft.name,
    notes: draft.notes,
    recording: context.recording,
    layout: context.layout as PresentationManifest["layout"],
    visibility: { entities: draft.entities, labels: draft.labels },
    measurements: draft.measurements,
    time: { start: Number(draft.time.start), end: Number(draft.time.end) },
    annotations: draft.annotations,
    evidence: draft.evidence,
    rate: draft.rate,
  });
  return { camera: captured.camera.bounds };
}
/** The outputs the picker offers: those of the chosen execution only, so a slow reply never offers the outputs
 * of the analysis chosen before. */
export function pickerOutputs<T extends { origin: EvidenceOrigin }>(detail: T | null, chosen: string): T | null {
  return detail && detail.origin.execution_id === chosen ? detail : null;
}
interface Listed {
  id: string;
  name: string;
  state: string;
  created: number;
  snapshot_hash: string;
  available: number;
  planned: number;
  partial: boolean;
}
function EvidencePicker({ client, connected, onAdd, count }: { client: StudioClient; connected: boolean; onAdd: (item: EvidenceSelection) => void; count: number }) {
  const [executions, setExecutions] = useState<Listed[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [loaded, setDetail] = useState<{ origin: EvidenceOrigin; outputs: OutputDescriptor[] } | null>(null);
  const [output, setOutput] = useState("");
  const [rows, setRows] = useState({ offset: "0", limit: "10", columns: [] as string[] });
  const [figure, setFigure] = useState({ width: "960", height: "540" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!connected) return;
    void client
      .command("analysis.list", { limit: 50 }, { revision: null })
      .then((r) => {
        if (!r.ok) throw new Error((r.error as { message?: string })?.message ?? "Listing failed");
        setExecutions((r.items as Listed[]).filter((e) => e.state === "succeeded"));
      })
      .catch((e) => setError(String(e)));
  }, [client, connected]);
  useEffect(() => {
    if (!chosen) {
      setDetail(null);
      return;
    }
    setBusy(true);
    void client
      .command("analysis.get", { execution_id: chosen }, { revision: null })
      .then((r) => {
        if (!r.ok) throw new Error((r.error as { message?: string })?.message ?? "Analysis unavailable");
        const d = r as unknown as { execution: { id: string; name: string }; snapshot: { hash: string }; output: unknown };
        const manifest = d.output ? (untag(d.output) as unknown as OutputManifest) : null;
        setDetail({ origin: { execution_id: d.execution.id, analysis_name: d.execution.name, snapshot_hash: d.snapshot.hash }, outputs: manifest?.outputs ?? [] });
        setOutput("");
      })
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  }, [client, chosen]);
  const detail = pickerOutputs(loaded, chosen);
  const selected = detail?.outputs.find((o) => o.id === output) ?? null;
  const fields = selected?.metadata.schema?.fields.map((f) => f.name) ?? [];
  const add = async () => {
    if (!selected || !detail) return;
    try {
      let choice: EvidenceChoice = {};
      if (selected.kind === "table") choice = { rows: { offset: Number(rows.offset), limit: Number(rows.limit), columns: rows.columns } };
      else if (selected.kind === "figure") choice = { figure: { width: Number(figure.width), height: Number(figure.height) } };
      onAdd(await evidenceFromOutput(selected, detail.origin, choice));
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };
  const full = count >= LIMITS.evidence;
  return (
    <div className="pv-picker-form">
      <Field label="Saved analysis" hint="Succeeded executions only; the frozen snapshot of the chosen one is named with each item">
        <select value={chosen} onChange={(e) => setChosen(e.target.value)} disabled={!executions}>
          <option value="">{executions ? "Choose an analysis" : "Loading analyses…"}</option>
          {executions?.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name} · {e.available} of {e.planned}{e.partial ? " · partial" : ""} · {formatTime(e.created)}
            </option>
          ))}
        </select>
      </Field>
      {detail && (
        <Field label="Output" hint="Number, table excerpt, distribution, figure or image">
          <select value={output} onChange={(e) => setOutput(e.target.value)}>
            <option value="">Choose an output</option>
            {detail.outputs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name} · {o.kind}
              </option>
            ))}
          </select>
        </Field>
      )}
      {selected?.kind === "table" && (
        <FieldGroup legend="Table excerpt" hint={`At most ${LIMITS.rows} rows and ${LIMITS.columns} columns`}>
          <Field label="First row" hint="0-based offset">
            <input inputMode="numeric" value={rows.offset} onChange={(e) => setRows({ ...rows, offset: e.target.value })} />
          </Field>
          <Field label="Rows" hint={`1 to ${LIMITS.rows}`}>
            <input inputMode="numeric" value={rows.limit} onChange={(e) => setRows({ ...rows, limit: e.target.value })} />
          </Field>
          <div className="ws-wide pv-picker">
            {fields.map((f) => (
              <CheckField key={f} label={f}>
                <input
                  type="checkbox"
                  checked={rows.columns.includes(f)}
                  {...disabledWhen(!rows.columns.includes(f) && rows.columns.length >= LIMITS.columns && `At most ${LIMITS.columns} columns`)}
                  onChange={(e) => setRows({ ...rows, columns: e.target.checked ? [...rows.columns, f] : rows.columns.filter((c) => c !== f) })}
                />
              </CheckField>
            ))}
            {!fields.length && <small className="ws-hint">All columns</small>}
          </div>
        </FieldGroup>
      )}
      {selected?.kind === "figure" && (
        <FieldGroup legend="Static view size" hint="Used by video and HTML exports">
          <Field label="Width" unit="px">
            <input inputMode="numeric" value={figure.width} onChange={(e) => setFigure({ ...figure, width: e.target.value })} />
          </Field>
          <Field label="Height" unit="px">
            <input inputMode="numeric" value={figure.height} onChange={(e) => setFigure({ ...figure, height: e.target.value })} />
          </Field>
        </FieldGroup>
      )}
      <Actions note="Labels (unit, population, sample size, convention, snapshot) are copied from the analysis and cannot be edited.">
        <button type="button" className="primary" {...disabledWhen(!selected && "Choose an output first", full && `At most ${LIMITS.evidence} evidence items`, busy && "Loading…")} onClick={() => void add()}>
          Add evidence
        </button>
      </Actions>
      {error && <FailureNotice message={error} compact />}
    </div>
  );
}
export function PresentationEditor({
  client,
  connected,
  source,
  recording,
  initial,
  saving,
  onSave,
  presentHash,
}: {
  client: StudioClient;
  connected: boolean;
  source: StageSource;
  recording: RecordingIdentity;
  initial: PresentationManifest | null;
  saving: boolean;
  onSave: (manifest: PresentationManifest) => Promise<void>;
  presentHash?: string;
}) {
  const [draft, setDraft] = useState<EditorDraft>(() => draftOf(initial));
  const [stage, setStage] = useState<StageHandle | null>(null);
  const stageRef = useRef<StageHandle | null>(null);
  const [error, setError] = useState("");
  const [picking, setPicking] = useState(false);
  const [dirty, setDirty] = useState(false);
  const edit = (patch: Partial<EditorDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  };
  const evidenceSource: EvidenceSource = useMemo(
    () => analysisEvidenceSource((type, body) => client.command(type, body, { revision: null }), (path, init) => client.fetchApi(path, init)),
    [client],
  );
  const ready = (handle: StageHandle) => {
    stageRef.current = handle;
    setStage(handle);
    const range = handle.recording.finiteRange;
    if (range && draft.time.start === "" && draft.time.end === "") setDraft((d) => ({ ...d, time: { start: String(range[0]), end: String(range[1]) } }));
  };
  const entities = useMemo(() => {
    const state = stage?.recording.trace.initialState ?? {};
    return Object.entries(state)
      .filter(([, v]) => !["job", "order"].includes(String((v as { $kind?: unknown })?.$kind)))
      .map(([id, v]) => ({ id, kind: String((v as { $kind?: unknown })?.$kind ?? "entity") }))
      .sort((a, b) => a.id.localeCompare(b.id))
      .slice(0, 500);
  }, [stage]);
  // Series keys arrive as trace chunks are read, so they are re-read until the list settles.
  const [series, setSeries] = useState<readonly string[]>([]);
  useEffect(() => {
    if (!stage) return;
    const read = () => {
      const keys = stage.recording.seriesKeys;
      setSeries((old) => (old.length === keys.length && old.every((k, i) => k === keys[i]) ? old : [...keys]));
    };
    read();
    const id = setInterval(read, 500);
    return () => clearInterval(id);
  }, [stage]);
  const range = stage?.recording.finiteRange ?? null;
  const useCurrentView = () => {
    const s = stageRef.current;
    if (!s) return;
    try {
      edit(currentView(draft, { recording, layout: s.recording.trace.layout }, s.scene, s.session, s.playback));
      setError("");
    } catch (e) {
      // The camera is still worth keeping even when another field is incomplete.
      edit({ camera: s.scene.worldBounds() });
      setError(e instanceof PresentationError ? `Saved framing taken; fix ${e.path} before saving` : String(e));
    }
  };
  const save = async () => {
    const s = stageRef.current;
    if (!s) return;
    try {
      const manifest = manifestOf(draft, recording, s.recording.trace.layout, draft.camera ?? s.scene.worldBounds(), initial);
      setError("");
      await onSave(manifest);
      setDirty(false);
    } catch (e) {
      setError(e instanceof PresentationError ? `${e.message}` : String(e));
    }
  };
  const toggle = (list: string[] | null, id: string, checked: boolean): string[] => {
    const current = list ?? [];
    return checked ? [...current, id] : current.filter((x) => x !== id);
  };
  return (
    <div className="pv-editor">
      <div>
        <section className="ws-card">
          <div className="ws-section-heading">
            <h2>Stage</h2>
            <span className="ws-hint">Pan, zoom and play freely; only "Use current view" and Save change the saved framing.</span>
          </div>
          <PresentationStage source={source} manifest={initial} range={false} onReady={ready} onError={setError} />
          <div className="pv-toolbar">
            <button type="button" onClick={() => stageRef.current?.scene.fit()}>Fit scene</button>
            <button type="button" className="primary" id="pv-use-view" onClick={useCurrentView} {...disabledWhen(!stage && "The recording is still opening")}>
              Use current view
            </button>
            <button type="button" {...disabledWhen(!draft.camera && "No saved framing yet")} onClick={() => draft.camera && stageRef.current?.scene.fitBounds(draft.camera)}>
              Reset to saved framing
            </button>
            <span className="ws-hint" data-camera={draft.camera ? draft.camera.map((v) => v.toFixed(2)).join(",") : ""}>
              {draft.camera ? `Saved framing ${draft.camera.map((v) => v.toFixed(2)).join(" · ")}` : "No saved framing yet; Save takes the current view"}
            </span>
          </div>
          {stage && <Transport stage={stageRef} range={range ?? [0, 1]} rate={draft.rate} />}
        </section>
        <section className="ws-card">
          <h2>Evidence</h2>
          <p className="ws-hint">Findings across replications from saved analyses, shown beside the animation with their qualifiers.</p>
          {draft.evidence.map((item) => (
            <div key={item.id} className="pv-evidence-row">
              <EvidenceItem item={item} source={evidenceSource(item.execution_id)} />
              <button type="button" className="ws-inline" onClick={() => edit({ evidence: draft.evidence.filter((e) => e.id !== item.id) })}>
                Remove {item.labels.name}
              </button>
            </div>
          ))}
          {!draft.evidence.length && <p className="ws-hint">No evidence selected.</p>}
          <Actions>
            <button type="button" onClick={() => setPicking(!picking)} {...disabledWhen(!connected && NOT_CONNECTED)}>
              {picking ? "Close evidence picker" : "Add evidence"}
            </button>
          </Actions>
          {picking && <EvidencePicker client={client} connected={connected} count={draft.evidence.length} onAdd={(item) => edit({ evidence: [...draft.evidence, item] })} />}
        </section>
      </div>
      <aside className="ws-stack">
        <section className="ws-card">
          <h2>Presentation</h2>
          <Field label="Name" required>
            <input value={draft.name} onChange={(e) => edit({ name: e.target.value })} />
          </Field>
          <Field label="Notes" hint="Plain text shown under the stage">
            <textarea value={draft.notes} onChange={(e) => edit({ notes: e.target.value })} />
          </Field>
          <FieldGroup legend="Time and rate">
            <Field label="Start" hint={range ? `Recording spans ${range[0]} to ${range[1]}` : undefined}>
              <input inputMode="decimal" value={draft.time.start} onChange={(e) => edit({ time: { ...draft.time, start: e.target.value } })} />
            </Field>
            <Field label="End">
              <input inputMode="decimal" value={draft.time.end} onChange={(e) => edit({ time: { ...draft.time, end: e.target.value } })} />
            </Field>
            <Field label="Rate">
              <select value={String(draft.rate)} onChange={(e) => edit({ rate: Number(e.target.value) })}>
                {[0.25, 0.5, 1, 2, 5, 10, 50].map((r) => (
                  <option key={r} value={r}>{r}×</option>
                ))}
              </select>
            </Field>
          </FieldGroup>
          <FieldGroup legend="Measurements" hint={`Recorded series shown as single-run values; at most ${LIMITS.measurements}`}>
            <div className="ws-wide pv-picker">
              {series.map((key) => (
                <CheckField key={key} label={key}>
                  <input
                    type="checkbox"
                    checked={draft.measurements.includes(key)}
                    {...disabledWhen(!draft.measurements.includes(key) && draft.measurements.length >= LIMITS.measurements && `At most ${LIMITS.measurements} measurements`)}
                    onChange={(e) => edit({ measurements: toggle(draft.measurements, key, e.target.checked) })}
                  />
                </CheckField>
              ))}
              {!series.length && <small className="ws-hint">{stage ? "This recording has no series." : "Opening the recording…"}</small>}
            </div>
          </FieldGroup>
          <FieldGroup legend="Visibility and labels">
            <CheckField label="Show every entity" hint="Untick to choose the visible entities">
              <input type="checkbox" checked={draft.entities === null} onChange={(e) => edit({ entities: e.target.checked ? null : entities.map((x) => x.id) })} />
            </CheckField>
            <CheckField label="Default label rule" hint="Untick to choose which entities are labelled">
              <input type="checkbox" checked={draft.labels === null} onChange={(e) => edit({ labels: e.target.checked ? null : [] })} />
            </CheckField>
            {(draft.entities !== null || draft.labels !== null) && (
              <div className="ws-wide pv-picker" role="group" aria-label="Entities">
                {entities.map((x) => (
                  <div key={x.id} className="pv-entity-row">
                    <code>{x.id}</code> <small>{x.kind}</small>
                    {draft.entities !== null && (
                      <CheckField label={`Show ${x.id}`}>
                        <input type="checkbox" checked={draft.entities.includes(x.id)} onChange={(e) => edit({ entities: toggle(draft.entities, x.id, e.target.checked) })} />
                      </CheckField>
                    )}
                    {draft.labels !== null && (
                      <CheckField label={`Label ${x.id}`}>
                        <input type="checkbox" checked={draft.labels.includes(x.id)} onChange={(e) => edit({ labels: toggle(draft.labels, x.id, e.target.checked) })} />
                      </CheckField>
                    )}
                  </div>
                ))}
              </div>
            )}
          </FieldGroup>
          <FieldGroup legend="Annotations" hint={`Plain text, optionally at a simulation time; at most ${LIMITS.annotations}`}>
            <div className="ws-wide">
              {draft.annotations.map((a, i) => (
                <div key={a.id} className="pv-annotation-row">
                  <Field label={`Annotation ${i + 1}`}>
                    <input value={a.text} onChange={(e) => edit({ annotations: draft.annotations.map((x) => (x.id === a.id ? { ...x, text: e.target.value } : x)) })} />
                  </Field>
                  <Field label={`Time of annotation ${i + 1}`} hint="Empty means the whole presentation">
                    <input inputMode="decimal" value={a.time === null ? "" : String(a.time)} onChange={(e) => edit({ annotations: draft.annotations.map((x) => (x.id === a.id ? { ...x, time: e.target.value === "" ? null : Number(e.target.value) } : x)) })} />
                  </Field>
                  <button type="button" className="ws-inline" onClick={() => edit({ annotations: draft.annotations.filter((x) => x.id !== a.id) })}>
                    Remove annotation {i + 1}
                  </button>
                </div>
              ))}
              <button type="button" className="ws-inline" {...disabledWhen(draft.annotations.length >= LIMITS.annotations && `At most ${LIMITS.annotations} annotations`)} onClick={() => edit({ annotations: [...draft.annotations, { id: crypto.randomUUID(), text: "", time: null }] })}>
                Add annotation
              </button>
            </div>
          </FieldGroup>
          {draft.evidence.length > 0 && (
            <details>
              <summary>Evidence qualifiers ({draft.evidence.length})</summary>
              {draft.evidence.map((e) => (
                <div key={e.id}>
                  <strong>{e.labels.name}</strong>
                  <EvidenceQualifiers labels={e.labels} />
                </div>
              ))}
            </details>
          )}
          <Actions note={dirty ? "Unsaved changes" : initial ? `Revision ${initial.revision}` : "Not saved yet"}>
            <button type="button" className="primary" id="pv-save" {...disabledWhen(!connected && NOT_CONNECTED, !stage && "The recording is still opening", saving && "Saving…")} onClick={() => void save()}>
              {initial ? "Save new revision" : "Save presentation"}
            </button>
            {presentHash && (
              <a className="ws-button" id="pv-present-button" href={presentHash}>
                Present →
              </a>
            )}
          </Actions>
          {error && <FailureNotice message={error} compact />}
        </section>
      </aside>
    </div>
  );
}
