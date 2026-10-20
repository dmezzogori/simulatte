/** The export entry of a saved presentation: opens the recording, wires the evidence source and the export-job
 * commands, and mounts the export dialog in presentation mode. */
import { useState } from "react";
import { HttpSource, openRecording, type RecordingView } from "@simulatte/trace";
import type { PresentationManifest } from "@simulatte/playback";
import { ExportDialog, type ExportJobs, type PresentationExport } from "../export-dialog";
import { evidenceSource } from "../artifacts/static-capture";
import type { Prefix, StudioClient } from "../studio-client";
import { disabledWhen, NOT_CONNECTED, READ_ONLY } from "../workspace/forms";
import playerStyle from "../../../player/src/player.css?raw";
import playerScript from "../../../player/dist/player.js?raw";
const MAX_TRACE = 100 * 1024 * 1024;
/** The recording bytes as one Blob, read in 1 MiB pieces through the authenticated prefix route. */
export async function traceBlob(source: HttpSource, limit: number, signal: AbortSignal): Promise<Blob> {
  if (source.size > limit) throw new RangeError("Offline HTML supports traces up to 100 MiB");
  const parts: BlobPart[] = [];
  for (let offset = 0; offset < source.size; offset += 1024 * 1024) {
    signal.throwIfAborted();
    parts.push(new Blob([(await source.read(offset, Math.min(1024 * 1024, source.size - offset), signal)) as BlobPart]));
  }
  return new Blob(parts);
}
/** ``export.admit`` → ``export.start`` → ``export.finish`` over the Studio client; replies carry ``job``. */
export function exportJobs(client: StudioClient): ExportJobs {
  const call = async (type: "export.admit" | "export.start" | "export.finish", body: Record<string, unknown>) => {
    const reply = await client.command(type, body, { revision: null });
    if (!reply.ok) throw new Error(((reply.error as { message?: string }) ?? {}).message ?? `${type} refused`);
    return reply.job as { id: string; snapshot_hash: string; state: string };
  };
  return {
    async admit(body) {
      const job = await call("export.admit", body as unknown as Record<string, unknown>);
      return { job_id: job.id, snapshot_hash: job.snapshot_hash };
    },
    async start(jobId) {
      await call("export.start", { job_id: jobId });
    },
    async finish(jobId, outcome) {
      await call("export.finish", { job_id: jobId, outcome });
    },
  };
}
export function PresentationExportButton({
  client,
  presentationId,
  manifest,
  prefix,
  connected,
  readOnly,
}: {
  client: StudioClient;
  presentationId: string;
  manifest: PresentationManifest;
  /** The recording's trace prefix, from ``run.get``. */
  prefix: Prefix | null;
  connected: boolean;
  readOnly: boolean;
}) {
  const [open, setOpen] = useState<{ recording: RecordingView; source: HttpSource } | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  const headers = { "X-Simulatte-Session": sessionStorage.getItem("simulatte-session") ?? "" };
  const start = async () => {
    if (!prefix) return;
    setOpening(true);
    setError("");
    try {
      const source = await HttpSource.open(prefix.url, headers);
      const recording = await openRecording(source);
      setOpen({ recording, source });
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setOpening(false);
    }
  };
  const presentationExport: PresentationExport | null = open
    ? {
        manifest,
        presentationId,
        revision: manifest.revision ?? 0,
        evidence: evidenceSource((type, body) => client.command(type, body, { revision: null }), (path, init) => client.fetchApi(path, init)),
        jobs: exportJobs(client),
        traceSource: (limit, signal) => traceBlob(open.source, Math.min(limit, MAX_TRACE), signal),
        playerScript,
        playerStyle,
      }
    : null;
  return (
    <>
      <button
        type="button"
        id="pv-export-button"
        {...disabledWhen(!connected && NOT_CONNECTED, readOnly && READ_ONLY, !prefix && "The recording has no committed trace yet", opening && "Opening the recording…")}
        onClick={() => void start()}
      >
        Export…
      </button>
      {error && (
        <small role="alert" className="pv-export-error">
          {error}
        </small>
      )}
      {open && presentationExport && <ExportDialog recording={open.recording} presentationExport={presentationExport} close={() => setOpen(null)} />}
    </>
  );
}
