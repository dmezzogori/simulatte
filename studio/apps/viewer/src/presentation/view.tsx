/** The presentation stage (one recording in a scene with playback) and the clean presentation mode. */
import { useEffect, useRef, useState, type RefObject } from "react";
import { HttpSource, openRecording, type Recording } from "@simulatte/trace";
import { Playback, Session, applyPresentation, enforceRange, type PresentationManifest } from "@simulatte/playback";
import { Scene } from "@simulatte/scene";
import type { Prefix } from "../studio-client";
import { formatValue } from "../analysis/codec";
import { EvidencePanel, type EvidenceSource } from "./evidence-panel";
import "./presentation.css";

export interface StageHandle {
  scene: Scene;
  session: Session;
  playback: Playback;
  recording: Recording;
}
/** The trace prefix and headers the stage needs to open a run. */
export interface StageSource {
  prefix: Prefix;
  headers: Readonly<Record<string, string>>;
}
type Kpis = Readonly<Record<string, { value: number | null }>>;
const clock = { request: (cb: (now: number) => void) => requestAnimationFrame(cb), cancel: (id: number) => cancelAnimationFrame(id) };
/** Opens the recording, runs the scene and applies the manifest once; temporary changes stay in the stage. */
export function PresentationStage({
  source,
  manifest,
  range = true,
  onReady,
  onFrame,
  onError,
  className = "pv-stage",
}: {
  source: StageSource;
  manifest: PresentationManifest | null;
  /** Keep playback inside the saved time range (presentation mode); the editor leaves it free. */
  range?: boolean;
  onReady?: (handle: StageHandle) => void;
  onFrame?: (time: number, kpis: Kpis, status: string) => void;
  onError?: (message: string) => void;
  className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ manifest, onReady, onFrame, onError });
  useEffect(() => {
    latest.current = { manifest, onReady, onFrame, onError };
  });
  useEffect(() => {
    const abort = new AbortController();
    let current: StageHandle | null = null;
    let release = () => {};
    void (async () => {
      try {
        const bytes = await HttpSource.open(source.prefix.url, source.headers, abort.signal);
        const recording = await openRecording(bytes, {}, abort.signal);
        if (abort.signal.aborted) return;
        const saved = latest.current.manifest;
        const session = new Session(recording, saved?.layout ?? null);
        const playback = new Playback(session, clock);
        const scene = new Scene();
        scene.onError = (message) => latest.current.onError?.(message);
        current = { scene, session, playback, recording };
        await scene.init(host.current!);
        if (abort.signal.aborted) return;
        let framed = false;
        const stop = playback.subscribe((frame, status, error) => {
          if (!frame) return;
          scene.setPlaying(status === "playing");
          scene.render(frame);
          if (!framed && saved) {
            framed = true;
            scene.fitBounds(saved.camera.bounds);
          }
          latest.current.onFrame?.(frame.time, frame.kpis, status);
          if (error) latest.current.onError?.(String(error));
        });
        release = stop;
        if (saved) {
          await applyPresentation(scene, session, playback, saved);
          if (range) {
            const unclamp = enforceRange(playback, saved.time);
            release = () => {
              unclamp();
              stop();
            };
          }
        } else {
          session.setSeries(recording.seriesKeys.slice(0, 8));
          await playback.seekTime(recording.finiteRange?.[0] ?? 0);
        }
        if (!abort.signal.aborted) latest.current.onReady?.(current);
      } catch (error) {
        if (!abort.signal.aborted) latest.current.onError?.(error instanceof Error ? error.message : String(error));
      }
    })();
    return () => {
      abort.abort();
      release();
      current?.playback.dispose();
      current?.session.dispose();
      current?.scene.dispose();
    };
  }, [source.prefix.url, manifest?.id, manifest?.revision]);
  return <div ref={host} className={className} aria-label="Simulation scene" />;
}
/** The single-run measurement values of the current frame, shown apart from cross-replication evidence. */
export function MeasurementStrip({ keys, kpis, time }: { keys: readonly string[]; kpis: Kpis | null; time: number | null }) {
  if (!keys.length) return null;
  return (
    <dl className="pv-measurements" aria-label="Single-run measurements">
      {keys.map((key) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd className="ws-num">{kpis && kpis[key] ? formatValue(kpis[key]!.value) : "—"}</dd>
        </div>
      ))}
      <div className="pv-clock">
        <dt>simulation time</dt>
        <dd className="ws-num">{time === null ? "—" : formatValue(time)}</dd>
      </div>
      <small>Values of this single run at the current time; cross-replication findings are in the evidence panel.</small>
    </dl>
  );
}
export function Transport({ stage, range, rate }: { stage: RefObject<StageHandle | null>; range: readonly [number, number]; rate: number }) {
  const [, redraw] = useState(0);
  const [time, setTime] = useState<number | null>(null);
  const [status, setStatus] = useState("paused");
  useEffect(() => {
    const id = setInterval(() => {
      const p = stage.current?.playback;
      if (!p) return;
      setTime(p.frame?.time ?? null);
      setStatus(p.status);
    }, 100);
    return () => clearInterval(id);
  }, [stage]);
  const playback = stage.current?.playback;
  const playing = status === "playing" || status === "buffering";
  return (
    <div className="pv-transport" role="group" aria-label="Playback">
      <button type="button" aria-label="Previous event" onClick={() => void playback?.step(-1)}>◀│</button>
      <button
        type="button"
        className="primary"
        onClick={() => {
          if (!playback) return;
          if (playing) playback.pause();
          else playback.play();
          redraw((n) => n + 1);
        }}
      >
        {playing ? "Pause" : "Play"}
      </button>
      <button type="button" aria-label="Next event" onClick={() => void playback?.step(1)}>│▶</button>
      <label>
        Rate
        <select
          defaultValue={String(rate)}
          onChange={(e) => {
            if (playback) playback.rate = Number(e.target.value);
          }}
        >
          {[0.25, 0.5, 1, 2, 5, 10, 50].map((r) => (
            <option key={r} value={r}>{r}×</option>
          ))}
        </select>
      </label>
      <input
        type="range"
        aria-label="Presentation time"
        min={range[0]}
        max={range[1]}
        step="any"
        value={time ?? range[0]}
        onChange={(e) => void playback?.seekTime(Number(e.target.value))}
      />
      <output aria-label="Current time">t = {time === null ? "—" : formatValue(time)}</output>
    </div>
  );
}
/** Clean presentation mode: the stage, measurements, the evidence panel and a reliable exit. */
export function PresentationView({
  manifest,
  source,
  evidence,
  onExit,
}: {
  manifest: PresentationManifest;
  source: StageSource;
  evidence: EvidenceSource | null;
  onExit: () => void;
}) {
  const stage = useRef<StageHandle | null>(null);
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [time, setTime] = useState<number | null>(null);
  const [error, setError] = useState("");
  const exit = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    exit.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onExit();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  return (
    <div className="pv-mode" role="region" aria-label={`Presentation: ${manifest.name}`}>
      <header className="pv-top">
        <h1>{manifest.name}</h1>
        <button ref={exit} type="button" onClick={onExit}>
          Exit presentation
        </button>
      </header>
      <div className="pv-body">
        <div className="pv-main">
          <PresentationStage
            source={source}
            manifest={manifest}
            onReady={(h) => {
              stage.current = h;
            }}
            onFrame={(t, k) => {
              setTime(t);
              setKpis(k);
            }}
            onError={setError}
          />
          <MeasurementStrip keys={manifest.measurements} kpis={kpis} time={time} />
          <Transport stage={stage} range={[manifest.time.start, manifest.time.end]} rate={manifest.rate} />
          {manifest.notes && <p className="pv-notes">{manifest.notes}</p>}
          {error && <p role="alert">{error}</p>}
        </div>
        <EvidencePanel manifest={manifest} source={evidence} />
      </div>
    </div>
  );
}
