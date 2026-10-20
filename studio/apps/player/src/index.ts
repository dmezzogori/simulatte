import notices from "../../../THIRD_PARTY_NOTICES.md?raw";
import { openRecording } from "@simulatte/trace";
import { Session, Playback } from "@simulatte/playback";
import { Scene } from "@simulatte/scene";
import { qualifierText, readPresentationBlock, viewRange, type PresentationBlock } from "./presentation-block";
const make = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string) => {
  const e = document.createElement(tag);
  if (text) e.textContent = text;
  return e;
};
const header = make("header"),
  title = make("strong", "simulatte · recorded run"),
  metadata = make("button", "Run information"),
  licenses = make("button", "Licenses"),
  fit = make("button", "Fit"),
  reset = make("button", "Reset view"),
  evidenceToggle = make("button", "Evidence"),
  host = make("main"),
  stage = make("div"),
  aside = make("aside"),
  evidence = make("section"),
  footer = make("footer"),
  back = make("button", "Previous event"),
  play = make("button", "Play"),
  next = make("button", "Next event"),
  rate = make("select"),
  time = make("output", "Opening…"),
  slider = make("input"),
  error = make("div"),
  diagnostics = make("div");
diagnostics.id = "diagnostics";
diagnostics.setAttribute("role", "status");
error.id = "error";
stage.id = "stage";
evidence.id = "evidence";
evidence.setAttribute("aria-label", "Presentation evidence");
evidence.hidden = true;
reset.hidden = true;
evidenceToggle.hidden = true;
slider.type = "range";
slider.setAttribute("aria-label", "Simulation time");
rate.setAttribute("aria-label", "Playback rate");
const rateOption = (n: number, selected = false) => {
  const o = make("option", `${n}×`);
  o.value = String(n);
  o.selected = selected;
  rate.append(o);
};
for (const n of [0.25, 0.5, 1, 2, 10, 50]) rateOption(n, n === 1);
header.append(
  title,
  metadata,
  licenses,
  fit,
  reset,
  evidenceToggle,
  make("small", "Dashed trails: transfer decoration"),
);
footer.append(back, play, next, rate, time, slider);
stage.append(aside);
host.append(stage, evidence);
document.body.append(header, host, diagnostics, footer, error);
const text = (value: unknown) => {
  let budget = 1000;
  const bounded = (v: unknown, depth: number): unknown => {
    if (--budget < 0 || depth > 5) return "[more recorded data]";
    if (typeof v === "string")
      return v.length > 4096 ? v.slice(0, 4096) + "…" : v;
    if (typeof v === "number" && !Number.isFinite(v)) return String(v);
    if (Array.isArray(v))
      return v.slice(0, 200).map((item) => bounded(item, depth + 1));
    if (v && typeof v === "object")
      return Object.fromEntries(
        Object.entries(v)
          .slice(0, 200)
          .map(([key, item]) => [key, bounded(item, depth + 1)]),
      );
    return v;
  };
  return JSON.stringify(bounded(value, 0), null, 2);
};
const scalar = (value: unknown): string => {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isNaN(value) ? "NaN" : value === Infinity ? "+∞" : value === -Infinity ? "−∞" : String(value);
  if (typeof value === "object") return text(value);
  return String(value);
};
/** The fixed evidence panel of a version-2 presentation: text and images only, every value from the block. */
function buildEvidence(block: PresentationBlock, seekTo: (t: number) => void): { measurements: Map<string, HTMLElement> } {
  const manifest = block.manifest!;
  const measurements = new Map<string, HTMLElement>();
  evidence.replaceChildren();
  const heading = make("h2", manifest.name);
  evidence.append(heading);
  if (manifest.notes) evidence.append(make("p", manifest.notes));
  if (manifest.measurements.length) {
    const dl = make("dl");
    dl.className = "measurements";
    for (const key of manifest.measurements) {
      const dd = make("dd", "—");
      measurements.set(key, dd);
      dl.append(make("dt", key), dd);
    }
    evidence.append(make("h3", "Measurements"), dl);
  }
  const statics = new Map(block.statics.map((s) => [s.evidence_id, s]));
  for (const excerpt of block.evidence) {
    const selection = manifest.evidence.find((e) => e.id === excerpt.id);
    if (!selection) continue;
    const item = make("article");
    item.append(make("h3", selection.labels.name));
    if (excerpt.kind === "number") {
      const value = make("p", excerpt.value + (selection.labels.unit ? " " + selection.labels.unit : ""));
      value.className = "value";
      item.append(value);
    } else if (excerpt.kind === "table") {
      const table = make("table"), head = make("thead"), body = make("tbody"), row = make("tr");
      for (const column of excerpt.columns) row.append(make("th", column));
      head.append(row);
      for (const cells of excerpt.rows) {
        const tr = make("tr");
        for (const cell of cells) tr.append(make("td", cell));
        body.append(tr);
      }
      table.append(head, body);
      item.append(table, make("small", `rows ${excerpt.offset + 1}–${excerpt.offset + excerpt.rows.length} of ${excerpt.total}`));
    } else if (excerpt.kind === "distribution") {
      const dl = make("dl");
      for (const [key, value] of excerpt.entries) dl.append(make("dt", key), make("dd", value));
      item.append(dl);
    } else {
      const view = statics.get(excerpt.static_id);
      if (view) {
        const image = make("img");
        image.src = view.url;
        image.width = view.width;
        image.height = view.height;
        image.alt = `Static view of ${selection.labels.name}`;
        item.append(image);
      } else item.append(make("p", "Static view missing"));
    }
    const qualifiers = make("small", qualifierText(selection.labels));
    qualifiers.className = "qualifiers";
    item.append(qualifiers);
    evidence.append(item);
  }
  if (manifest.annotations.length) {
    evidence.append(make("h3", "Annotations"));
    const list = make("ol");
    for (const annotation of manifest.annotations) {
      const li = make("li");
      if (annotation.time !== null) {
        const jump = make("button", `t = ${scalar(annotation.time)}`);
        const at = annotation.time;
        jump.onclick = () => seekTo(at);
        li.append(jump, " ");
      }
      li.append(annotation.text);
      list.append(li);
    }
    evidence.append(list);
  }
  const provenance = make(
    "p",
    `presentation ${(manifest.id ?? "unsaved").slice(0, 8)} r${manifest.revision ?? 0} · recording ${manifest.recording.attempt_id.slice(0, 8)} · snapshot ${(block.snapshot_hash ?? "").slice(0, 12)}`,
  );
  provenance.className = "provenance";
  evidence.append(provenance);
  return { measurements };
}
async function start() {
  const encoded = document.getElementById("trace")!;
  const b64 = encoded.textContent ?? "";
  encoded.remove();
  const padding = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(Math.floor(b64.length / 4) * 3 - padding);
  let written = 0;
  for (let offset = 0; offset < b64.length; offset += 65536) {
    const part = atob(b64.slice(offset, offset + 65536));
    for (let i = 0; i < part.length; i++) bytes[written++] = part.charCodeAt(i);
    if (offset % (65536 * 64) === 0)
      await new Promise((resolve) => setTimeout(resolve, 0));
  }
  const recording = await openRecording(bytes.buffer);
  const presentation = document.getElementById("presentation");
  let block: PresentationBlock | null = null;
  if (presentation) {
    const data = JSON.parse(presentation.textContent ?? "null");
    presentation.remove();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    block = readPresentationBlock(data, hash);
  }
  const manifest = block?.manifest ?? null;
  const session = new Session(recording, (block?.layout as Parameters<typeof Session.prototype.setPresentation>[0]) ?? null);
  session.setSeries(manifest ? manifest.measurements : recording.seriesKeys.slice(0, 8));
  const playback = new Playback(session),
    scene = new Scene() as Scene & {
      fitBounds?(bounds: readonly [number, number, number, number]): void;
      setVisibility?(selection: { entities: Set<string> | null; labels: Set<string> | null }): void;
    };
  await scene.init(stage);
  const range = recording.finiteRange;
  const view = range ? viewRange(manifest, range) : null;
  let selected: string | null = null,
    showMetadata = false,
    showLicenses = false,
    framed = false;
  const measurementCells = manifest ? buildEvidence(block!, (t) => void playback.seekTime(Math.max(view!.start, Math.min(view!.end, t)))).measurements : new Map<string, HTMLElement>();
  const applyView = () => {
    if (!manifest) return;
    if (typeof scene.setVisibility === "function")
      scene.setVisibility({
        entities: manifest.visibility.entities ? new Set(manifest.visibility.entities) : null,
        labels: manifest.visibility.labels ? new Set(manifest.visibility.labels) : null,
      });
    if (typeof scene.fitBounds === "function") scene.fitBounds(manifest.camera.bounds);
    else scene.fit();
  };
  if (manifest) {
    if (!Array.from(rate.options).some((o) => Number(o.value) === manifest.rate)) rateOption(manifest.rate);
    rate.value = String(manifest.rate);
    playback.rate = manifest.rate;
    title.textContent = `simulatte · ${manifest.name}`;
    reset.hidden = false;
    evidenceToggle.hidden = false;
    evidence.hidden = false;
    evidenceToggle.setAttribute("aria-pressed", "true");
  }
  scene.onSelect = (id) => {
    selected = id;
    scene.select(id);
    if (playback.frame) update();
  };
  scene.onError = (message) => {
    error.textContent = message;
  };
  const update = () => {
    const frame = playback.frame;
    if (!frame) return;
    aside.textContent = showLicenses
      ? notices
      : showMetadata
        ? text({
            manifest: recording.trace.manifest,
            outcome: recording.trace.outcome,
            truncated: recording.trace.truncated,
            diagnostics: frame.diagnostics,
            finalScalars: recording.scalars,
            entityKinds: recording.kinds,
            eventCatalog: recording.catalog,
          })
        : selected
          ? text({
              entity: selected,
              state: frame.state[selected] ?? "Not present at this cursor",
              schema: recording.kinds[String(frame.state[selected]?.["$kind"])],
            })
          : manifest
            ? ""
            : text({ kpis: frame.kpis });
    aside.hidden = !aside.textContent || aside.textContent === "{}";
    for (const [key, cell] of measurementCells) {
      const sample = (frame.kpis as Record<string, unknown>)[key] as { value?: unknown } | number | null | undefined;
      cell.textContent = scalar(sample !== null && typeof sample === "object" ? sample.value : sample);
    }
  };
  playback.subscribe((frame, status, failure) => {
    scene.setPlaying(status === "playing");
    if (failure) error.textContent = String(failure);
    // The saved interval ends here: pause and hold the last frame instead of running on.
    if (view && frame && (status === "playing" || status === "buffering") && frame.time >= view.end) playback.pause();
    const playLabel = status === "playing" || status === "buffering" ? "Pause" : "Play";
    // Preserve the pressed text node across frames so WebKit delivers the click.
    if (play.textContent !== playLabel) play.textContent = playLabel;
    play.disabled =
      !frame ||
      !recording.finiteRange ||
      !Number.isFinite(frame.time) ||
      status === "ended" ||
      (view !== null && frame.time >= view.end);
    back.disabled =
      !frame || frame.mode === "activation" || recording.trace.level !== "full";
    next.disabled =
      !recording.historyEnd ||
      recording.trace.level !== "full" ||
      (frame?.cursor?.[0] === recording.historyEnd[0] &&
        frame?.cursor?.[1] === recording.historyEnd[1]);
    if (frame) {
      diagnostics.textContent = frame.diagnostics.join(" · ");
      diagnostics.hidden = !frame.diagnostics.length;
      scene.render(frame);
      if (!framed) {
        framed = true;
        applyView();
      }
      time.textContent = `t = ${frame.time.toLocaleString("en", { maximumFractionDigits: 4 })}${frame.mode === "exact" ? " · seq " + frame.cursor?.[1] : ""}`;
      slider.value = String(frame.time);
      update();
    }
  });
  play.onclick = () => {
    if (playback.status === "playing" || playback.status === "buffering")
      playback.pause();
    else {
      if (view && playback.frame && playback.frame.time >= view.end) void playback.seekTime(view.start).then(() => playback.play());
      else playback.play();
    }
  };
  back.onclick = () => {
    void playback.step(-1);
  };
  next.onclick = () => {
    void playback.step(1);
  };
  rate.onchange = () => {
    playback.rate = Number(rate.value);
  };
  fit.onclick = () => scene.fit();
  reset.onclick = () => {
    if (!manifest || !view) return;
    applyView();
    rate.value = String(manifest.rate);
    playback.rate = manifest.rate;
    scene.resetTransfers();
    void playback.seekTime(view.start);
  };
  evidenceToggle.onclick = () => {
    evidence.hidden = !evidence.hidden;
    evidenceToggle.setAttribute("aria-pressed", String(!evidence.hidden));
  };
  licenses.onclick = () => {
    showLicenses = !showLicenses;
    update();
  };
  metadata.onclick = () => {
    showLicenses = false;
    showMetadata = !showMetadata;
    update();
  };
  slider.oninput = () => {
    scene.resetTransfers();
    const wanted = Number(slider.value);
    void playback.seekTime(view ? Math.max(view.start, Math.min(view.end, wanted)) : wanted);
  };
  slider.disabled = !range;
  slider.min = String(view?.start ?? range?.[0] ?? 0);
  slider.max = String(view?.end ?? range?.[1] ?? 1);
  slider.step = "any";
  document.addEventListener("visibilitychange", () =>
    playback.setHidden(document.hidden),
  );
  window.addEventListener("pagehide", () => {
    playback.dispose();
    scene.dispose();
  });
  const at = recording.trace.activation;
  if (view && manifest) await playback.seekTime(view.start);
  else
    await playback.seek(
      at
        ? { mode: "activation", time: at[0], cursor: at }
        : { mode: "continuous", time: range?.[0] ?? 0, cursor: null },
    );
}
void start().catch((reason) => {
  error.textContent = `Unable to open this recording: ${String(reason)}`;
});
