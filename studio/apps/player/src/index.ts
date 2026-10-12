import notices from "../../../THIRD_PARTY_NOTICES.md?raw";
import { openRecording } from "@simulatte/trace";
import { Session, Playback } from "@simulatte/playback";
import { Scene } from "@simulatte/scene";
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
  host = make("main"),
  aside = make("aside"),
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
slider.type = "range";
slider.setAttribute("aria-label", "Simulation time");
rate.setAttribute("aria-label", "Playback rate");
for (const n of [0.25, 0.5, 1, 2, 10, 50]) {
  const o = make("option", `${n}×`);
  o.value = String(n);
  o.selected = n === 1;
  rate.append(o);
}
header.append(
  title,
  metadata,
  licenses,
  fit,
  make("small", "Dashed trails: transfer decoration"),
);
footer.append(back, play, next, rate, time, slider);
host.append(aside);
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
  const presentation = document.getElementById('presentation');
  let overlay = null;
  if (presentation) {
    const data = JSON.parse(presentation.textContent ?? 'null');
    presentation.remove();
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (!data || data.version !== 1 || Object.keys(data).sort().join(',') !== 'layout,trace_sha256,version' || data.trace_sha256 !== hash)
      throw new Error('Presentation metadata does not match this trace');
    overlay = data.layout;
  }
  const session = new Session(recording, overlay);
  session.setSeries(recording.seriesKeys.slice(0, 8));
  const playback = new Playback(session),
    scene = new Scene();
  await scene.init(host);
  let selected: string | null = null,
    showMetadata = false,
    showLicenses = false;
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
          : text({ kpis: frame.kpis });
    aside.hidden = !aside.textContent || aside.textContent === "{}";
  };
  playback.subscribe((frame, status, failure) => {
    scene.setPlaying(status === "playing");
    if (failure) error.textContent = String(failure);
    const playLabel = status === "playing" || status === "buffering" ? "Pause" : "Play";
    // Preserve the pressed text node across frames so WebKit delivers the click.
    if (play.textContent !== playLabel) play.textContent = playLabel;
    play.disabled =
      !frame ||
      !recording.finiteRange ||
      !Number.isFinite(frame.time) ||
      status === "ended";
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
      time.textContent = `t = ${frame.time.toLocaleString("en", { maximumFractionDigits: 4 })}${frame.mode === "exact" ? " · seq " + frame.cursor?.[1] : ""}`;
      slider.value = String(frame.time);
      update();
    }
  });
  play.onclick = () => {
    if (playback.status === "playing" || playback.status === "buffering")
      playback.pause();
    else playback.play();
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
    void playback.seekTime(Number(slider.value));
  };
  const range = recording.finiteRange;
  slider.disabled = !range;
  slider.min = String(range?.[0] ?? 0);
  slider.max = String(range?.[1] ?? 1);
  slider.step = "any";
  document.addEventListener("visibilitychange", () =>
    playback.setHidden(document.hidden),
  );
  window.addEventListener("pagehide", () => {
    playback.dispose();
    scene.dispose();
  });
  const at = recording.trace.activation;
  await playback.seek(
    at
      ? { mode: "activation", time: at[0], cursor: at }
      : { mode: "continuous", time: range?.[0] ?? 0, cursor: null },
  );
}
void start().catch((reason) => {
  error.textContent = `Unable to open this recording: ${String(reason)}`;
});
