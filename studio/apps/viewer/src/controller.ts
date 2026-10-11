import { inspectValue, clearInspection } from "./value-inspector";
import { createDockview, themeLight, type DockviewApi } from "dockview";
import uPlot from "uplot";
import {
  openRecording,
  HttpSource,
  type Recording,
  compare,
  type Cursor,
  type TraceEvent,
} from "@simulatte/trace";
import { Session, Playback, type PreparedFrame } from "@simulatte/playback";
import { Scene } from "@simulatte/scene";
export const format = (v: unknown): string =>
  JSON.stringify(
    v,
    (_key, value: unknown) =>
      typeof value === "number" && !Number.isFinite(value)
        ? Number.isNaN(value)
          ? "NaN"
          : value > 0
            ? "+∞"
            : "−∞"
        : value,
    2,
  ) ?? "—";
const number = (v: number) =>
  Number.isFinite(v)
    ? v.toLocaleString("en", { maximumFractionDigits: 4 })
    : v > 0
      ? "+∞"
      : "−∞";
const el = <K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
  cls?: string,
): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
export class Controller {
  playback: Playback | null = null;
  recording: Recording | null = null;
  source: Blob | HttpSource | null = null;
  name = "";
  scene: Scene | null = null;
  dock: DockviewApi;
  #panels = new Map<string, HTMLElement>();
  #generation = 0;
  #abort: AbortController | null = null;
  #selected: string | null = null;
  #entitySearch = "";
  #entityPage = 0;
  #entities: HTMLDivElement;
  #fields: HTMLDivElement;
  #schema: HTMLDivElement;
  #schemaKey = "";
  #entityTitle: HTMLHeadingElement;
  #history: HTMLDivElement;
  #details: HTMLDivElement;
  #after: Cursor | undefined;
  #phase: "simulation" | "prelude" = "simulation";
  #filter = "";
  #eventType = "";
  #eventEntity = "";
  #eventStart: number | null = null;
  #eventEnd: number | null = null;
  #series: string[] = [];
  #seriesPicker: HTMLDivElement;
  #chartGeneration = 0;
  #historyGeneration = 0;
  #tiles: HTMLDivElement;
  #charts: HTMLDivElement;
  #plots: uPlot[] = [];
  #chartResize: ResizeObserver[] = [];
  #tileValues = new Map<string, HTMLElement>();
  #clean: (() => void)[] = [];
  #frame: PreparedFrame | null = null;
  #retiredGeneration = 0;
  #retiredKey: string | null = null;
  onInfo: ((name: string, info: string) => void) | null = null;
  onError: ((message: string) => void) | null = null;
  constructor(
    host: HTMLElement,
    readonly timeline: HTMLInputElement,
    readonly time: HTMLElement,
    readonly status: HTMLElement,
    readonly playButton: HTMLButtonElement,
  ) {
    const scene = el("div", undefined, "scene-host");
    this.#panels.set("scene", scene);
    const legend = el(
      "small",
      "Dashed trails: transfer decoration",
      "scene-legend",
    );
    scene.append(legend);
    const inspector = el("div", undefined, "inspector panel-padding");
    this.#panels.set("inspector", inspector);
    const search = el("input");
    search.placeholder = "Find an entity";
    search.setAttribute("aria-label", "Find an entity");
    search.oninput = () => {
      this.#entitySearch = search.value;
      this.#entityPage = 0;
      this.updateInspector();
    };
    this.#entities = el("div", undefined, "entities");
    this.#entityTitle = el("h3", "Select an entity");
    this.#fields = el("div");
    const schema = el("details");
    schema.hidden = true;
    schema.append(el("summary", "Entity schema"));
    this.#schema = el("div");
    schema.append(this.#schema);
    const prev = el("button", "Previous entities"),
      next = el("button", "Next entities");
    prev.onclick = () => {
      this.#entityPage = Math.max(0, this.#entityPage - 1);
      this.updateInspector();
    };
    next.onclick = () => {
      this.#entityPage++;
      this.updateInspector();
    };
    inspector.append(
      search,
      this.#entities,
      prev,
      next,
      this.#entityTitle,
      this.#fields,
      schema,
    );
    const history = el("div", undefined, "history panel-padding");
    this.#panels.set("events", history);
    const toolbar = el("div", undefined, "row");
    const phase = el("select");
    phase.setAttribute("aria-label", "Event phase");
    for (const [value, label] of [
      ["simulation", "Simulation events"],
      ["prelude", "Preparation log"],
    ]) {
      const o = el("option", label);
      o.value = value!;
      phase.append(o);
    }
    phase.onchange = () => {
      this.#phase = phase.value as "simulation" | "prelude";
      this.#after = undefined;
      void this.history();
    };
    const filter = el("input");
    filter.placeholder = "Payload text search";
    filter.setAttribute("aria-label", "Payload text search");
    filter.onchange = () => {
      this.#filter = filter.value;
      this.#after = undefined;
      void this.history();
    };
    const first = el("button", "First page"),
      more = el("button", "Next page");
    first.onclick = () => {
      this.#after = undefined;
      void this.history();
    };
    more.onclick = () => {
      void this.history();
    };
    const type = el("input"),
      entity = el("input"),
      start = el("input"),
      end = el("input");
    for (const [input, label] of [
      [type, "Event type"],
      [entity, "Changed entity"],
      [start, "From time"],
      [end, "Through time"],
    ] as const) {
      input.placeholder = label;
      input.setAttribute("aria-label", label);
    }
    start.type = end.type = "number";
    start.step = end.step = "any";
    const filters = () => {
      this.#eventType = type.value;
      this.#eventEntity = entity.value;
      this.#eventStart = start.value ? Number(start.value) : null;
      this.#eventEnd = end.value ? Number(end.value) : null;
      this.#after = undefined;
      void this.history();
    };
    type.onchange = entity.onchange = start.onchange = end.onchange = filters;
    entity.title =
      "Filters delta-touched entities; payload references are not inferred.";
    toolbar.append(phase, filter, type, entity, start, end, first, more);
    this.#history = el("div", undefined, "event-rows");
    this.#details = el("div");
    history.append(toolbar, this.#history, this.#details);
    const kpis = el("div", undefined, "panel-padding");
    this.#panels.set("kpis", kpis);
    this.#tiles = el("div", undefined, "tiles");
    this.#charts = el("div", undefined, "charts");
    this.#seriesPicker = el("div", undefined, "series-picker");
    kpis.append(this.#seriesPicker, this.#tiles, this.#charts);
    this.dock = createDockview(host, {
      theme: themeLight,
      disableFloatingGroups: true,
      createComponent: (options) => ({
        element: this.#panels.get(options.name)!,
        init: () => {},
      }),
    });
    this.resetLayout();
    try {
      const saved = localStorage.getItem("simulatte-panel-layout-v1");
      if (saved && saved.length < 100000) {
        const layout = JSON.parse(saved);
        if (
          layout.popoutGroups?.length ||
          Object.keys(layout.panels ?? {}).some((key) => !this.#panels.has(key))
        )
          throw new Error("Unknown saved panel");
        this.dock.fromJSON(layout);
        if (!this.dock.getPanel("scene")) this.resetLayout();
      }
    } catch {
      this.resetLayout();
    }
    let save: ReturnType<typeof setTimeout> | undefined;
    const changes = this.dock.onDidLayoutChange(() => {
      clearTimeout(save);
      save = setTimeout(() => {
        try {
          localStorage.setItem(
            "simulatte-panel-layout-v1",
            JSON.stringify(this.dock.toJSON()),
          );
        } catch {
          /* Storage is optional for preferences. */
        }
      }, 250);
    });
    this.#clean.push(() => {
      clearTimeout(save);
      changes.dispose();
    });
    const hidden = () => this.playback?.setHidden(document.hidden);
    document.addEventListener("visibilitychange", hidden);
    this.#clean.push(() =>
      document.removeEventListener("visibilitychange", hidden),
    );
  }
  resetLayout(): void {
    this.dock.clear();
    this.dock.addPanel({ id: "scene", component: "scene", title: "Scene" });
    this.dock.addPanel({
      id: "inspector",
      component: "inspector",
      title: "Inspector",
      position: { referencePanel: "scene", direction: "right" },
      initialWidth: 310,
    });
    this.dock.addPanel({
      id: "kpis",
      component: "kpis",
      title: "Recorded KPIs",
      position: { referencePanel: "scene", direction: "below" },
      initialHeight: 230,
    });
    this.dock.addPanel({
      id: "events",
      component: "events",
      title: "Events",
      position: { referencePanel: "kpis", direction: "within" },
    });
  }
  present(enabled: boolean): void {
    const panel = this.dock.getPanel("scene");
    if (enabled && panel) this.dock.maximizeGroup(panel);
    else this.dock.exitMaximizedGroup();
  }
  async open(source: Blob | HttpSource, name: string): Promise<void> {
    const generation = ++this.#generation;
    this.#abort?.abort();
    this.#historyGeneration++;
    this.#retiredGeneration++;
    this.#chartGeneration++;
    this.#retiredKey = null;
    this.recording = null;
    clearInspection(this.#fields);
    clearInspection(this.#details);
    const abort = new AbortController();
    this.#abort = abort;
    this.playback?.dispose();
    this.scene?.dispose();
    this.scene = null;
    this.#frame = null;
    this.#selected = null;
    this.source = source;
    this.name = name;
    this.status.textContent = "Opening trace…";
    try {
      const recording = await openRecording(source, {}, abort.signal);
      if (generation !== this.#generation) return;
      this.recording = recording;
      const session = new Session(recording);
      this.#schemaKey = "";
      this.#series = recording.seriesKeys.slice(0, 8);
      session.setSeries(this.#series);
      this.#seriesPicker.replaceChildren();
      const select = el("select");
      select.setAttribute("aria-label", "Recorded series");
      for (const key of recording.seriesKeys) {
        const option = el("option", key);
        option.value = key;
        select.append(option);
      }
      const add = el("button", "Add series"),
        clear = el("button", "Clear series");
      const refresh = async () => {
        session.setSeries(this.#series);
        await this.charts();
        if (this.#frame) await this.playback?.seek(this.#frame);
      };
      add.onclick = () => {
        if (!this.#series.includes(select.value) && this.#series.length < 8) {
          this.#series.push(select.value);
          void refresh().catch((e) => this.onError?.(String(e)));
        }
      };
      clear.onclick = () => {
        this.#series = [];
        void refresh().catch((e) => this.onError?.(String(e)));
      };
      this.#seriesPicker.append(
        select,
        add,
        clear,
        el("small", "Up to eight active series"),
      );
      const playback = new Playback(session);
      this.playback = playback;
      const scene = new Scene();
      this.scene = scene;
      await scene.init(this.#panels.get("scene")!);
      if (generation !== this.#generation) {
        scene.dispose();
        return;
      }
      scene.onSelect = (id) => {
        this.#selected = id;
        scene.select(id);
        clearInspection(this.#fields);
        this.updateInspector();
      };
      scene.onError = (message) => this.onError?.(message);
      playback.subscribe((frame, status, error) => {
        if (generation !== this.#generation) return;
        scene.setPlaying(status === "playing");
        this.status.textContent = status;
        this.status.title = frame?.diagnostics.join("\n") ?? "";
        const current = frame?.cursor;
        this.playButton.disabled =
          !frame ||
          !recording.finiteRange ||
          !Number.isFinite(frame.time) ||
          status === "ended";
        const previous =
          this.playButton.parentElement?.querySelector<HTMLButtonElement>(
            '[aria-label="Previous event"]',
          );
        const next =
          this.playButton.parentElement?.querySelector<HTMLButtonElement>(
            '[aria-label="Next event"]',
          );
        if (previous)
          previous.disabled =
            recording.trace.level !== "full" ||
            !frame ||
            frame.mode === "activation";
        if (next)
          next.disabled =
            recording.trace.level !== "full" ||
            !recording.historyEnd ||
            !!(current && compare(current, recording.historyEnd) >= 0);
        this.playButton.textContent =
          status === "playing" || status === "buffering" ? "Pause" : "Play";
        if (error) this.onError?.(String(error));
        if (frame && frame !== this.#frame) {
          this.#frame = frame;
          scene.render(frame);
          this.time.textContent = `t = ${number(frame.time)}${frame.mode === "exact" ? " · seq " + frame.cursor?.[1] : frame.mode === "activation" ? " · activation" : ""}`;
          this.timeline.value = String(frame.time);
          this.updateInspector();
          this.updateKpis(frame);
        }
      });
      const range = recording.finiteRange;
      this.timeline.disabled = !range;
      this.timeline.min = String(range?.[0] ?? 0);
      this.timeline.max = String(range?.[1] ?? 1);
      this.timeline.step = "any";
      this.onInfo?.(
        name,
        `${recording.trace.level} trace · ${recording.trace.outcome ?? "incomplete"} · ${recording.trace.layout?.schema_version === "1.0" ? "recorded layout" : "viewer fallback layout"}${recording.trace.level === "kpi" ? " · KPI-only: no entity animation or event stepping" : ""}`,
      );
      await this.charts();
      this.#after = undefined;
      await this.history();
      const activation = recording.trace.activation;
      await playback.seek(
        activation
          ? { mode: "activation", time: activation[0], cursor: activation }
          : { mode: "continuous", time: range?.[0] ?? 0, cursor: null },
      );
    } catch (error) {
      if (generation === this.#generation) {
        this.status.textContent = "Unable to open trace";
        this.onError?.(error instanceof Error ? error.message : String(error));
      }
    }
  }
  private updateInspector(): void {
    const frame = this.#frame;
    if (!frame) return;
    const ids = Object.keys(frame.state)
      .filter((id) =>
        id.toLowerCase().includes(this.#entitySearch.toLowerCase()),
      )
      .sort();
    this.#entityPage = Math.min(
      this.#entityPage,
      Math.max(0, Math.ceil(ids.length / 200) - 1),
    );
    const visible = ids.slice(
      this.#entityPage * 200,
      (this.#entityPage + 1) * 200,
    );
    const signature = JSON.stringify([visible, this.#selected]);
    if (this.#entities.dataset["signature"] !== signature) {
      this.#entities.dataset["signature"] = signature;
      this.#entities.replaceChildren(
        ...visible.map((id) => {
          const b = el("button", id, id === this.#selected ? "selected" : "");
          b.onclick = () => {
            this.#selected = id;
            this.scene?.select(id);
            clearInspection(this.#fields);
            this.updateInspector();
          };
          return b;
        }),
      );
    }
    if (!this.#selected) {
      this.#schema.replaceChildren();
      this.#schema.parentElement!.hidden = true;
      this.#schemaKey = "";
      this.#entityTitle.textContent = `${ids.length} entities · select to inspect`;
      this.#fields.textContent = "";
      return;
    }
    const fields = frame.state[this.#selected];
    this.#entityTitle.textContent =
      this.#selected + (fields ? "" : " · not present at this cursor");
    if (fields) {
      this.showSchema(fields);
      this.#retiredGeneration++;
      this.#retiredKey = null;
      inspectValue(this.#fields, fields);
    } else if (this.#retiredKey !== `${this.#selected}:${frame.cursor}`) {
      this.#retiredKey = `${this.#selected}:${frame.cursor}`;
      this.#schema.parentElement!.hidden = true;
      this.#schemaKey = "";
      this.#fields.textContent =
        "Not present in the live state. Loading last recorded state…";
      void this.retired(this.#selected, frame.cursor).catch((error) =>
        this.onError?.(String(error)),
      );
    }
  }
  private async retired(id: string, cursor: Cursor | null): Promise<void> {
    const generation = ++this.#retiredGeneration,
      recording = this.recording;
    if (!recording || !cursor) return;
    const events = await recording.events({
      entity: id,
      through: cursor,
      direction: "backward",
      limit: 1,
    });
    const e = events[0];
    if (!e || !e.deltas.some((op) => op[0] === "retire")) {
      if (generation === this.#retiredGeneration)
        this.#fields.textContent = "No earlier state is available.";
      return;
    }
    if (generation === this.#retiredGeneration)
      this.#entityTitle.textContent = `${id} · retired at this cursor`;
    const before = await recording.events({
      before: [e.t, e.seq],
      direction: "backward",
      limit: 1,
    });
    const prev = before[0];
    const at = recording.domainCursor(
      prev ? [prev.t, prev.seq] : recording.trace.activation!,
    );
    if (!at) return;
    const state = await recording.trace.replay().prepare(at);
    if (generation === this.#retiredGeneration) {
      if (state[id]) this.showSchema(state[id]);
      inspectValue(this.#fields, {
        "Last state before retirement": { time: at[0], seq: at[1] },
        state: state[id],
      });
    }
  }
  private showSchema(fields: Record<string, unknown>): void {
    this.#schema.parentElement!.hidden = false;
    const kind = String(fields["$kind"]);
    if (kind === this.#schemaKey) return;
    this.#schemaKey = kind;
    clearInspection(this.#schema);
    inspectValue(
      this.#schema,
      this.recording?.kinds[kind] ?? { kind, schema: "Not recorded" },
    );
  }
  async history(): Promise<void> {
    const r = this.recording;
    if (!r) return;
    const generation = ++this.#historyGeneration;
    const phase = this.#phase;
    try {
      const rows = await r.events({
        phase,
        limit: 200,
        ...(this.#after
          ? { after: this.#after }
          : this.#eventStart !== null
            ? { after: [this.#eventStart, -1] as Cursor }
            : {}),
        ...(this.#eventEnd !== null
          ? { through: [this.#eventEnd, Number.MAX_SAFE_INTEGER] as Cursor }
          : {}),
        ...(this.#eventType ? { type: this.#eventType } : {}),
        ...(this.#eventEntity ? { entity: this.#eventEntity } : {}),
        ...(this.#filter ? { text: this.#filter } : {}),
      });
      if (generation !== this.#historyGeneration) return;
      this.#history.replaceChildren(
        ...rows.map((e) => {
          const b = el("button", `${number(e.t)}  ·  ${e.seq}  ·  ${e.type}`);
          b.onclick = () => {
            clearInspection(this.#details);
            inspectValue(this.#details, {event:e, catalog:r.catalog[e.type] ?? "Unknown event type"});
            if (phase === "simulation") {
              this.scene?.resetTransfers();
              void this.playback?.seek({
                mode: "exact",
                time: e.t,
                cursor: [e.t, e.seq],
              });
            }
          };
          return b;
        }),
      );
      const last = rows.at(-1);
      if (last) this.#after = [last.t, last.seq];
    } catch (error) {
      if (generation === this.#historyGeneration && r === this.recording)
        this.onError?.(String(error));
    }
  }
  private async charts(): Promise<void> {
    const generation = ++this.#chartGeneration;
    this.#plots.forEach((p) => p.destroy());
    this.#plots = [];
    this.#chartResize.forEach((r) => r.disconnect());
    this.#chartResize = [];
    this.#charts.replaceChildren();
    this.#tiles.replaceChildren();
    this.#tileValues.clear();
    const recording = this.recording;
    if (!recording) return;
    for (const key of this.#series) {
      const tile = el("div", undefined, "tile");
      const title = el("div", key, "muted");
      const value = el("strong", "—");
      tile.append(
        title,
        value,
        el("small", "No eligible sample", "sample-source"),
      );
      this.#tiles.append(tile);
      this.#tileValues.set(key, value);
      const declaration = recording.trace.kpiDeclarations[key];
      tile.title = format({
        declaration,
        display: recording.seriesDisplay[key] ?? "Assumed: previous, gap",
      });
      const range = recording.finiteRange;
      if (!range) continue;
      const data = await recording.chart(key, range);
      if (this.recording !== recording || generation !== this.#chartGeneration)
        return;
      const host = el("div", undefined, "chart");
      this.#charts.append(host);
      const interpolation =
        recording.seriesDisplay[key]?.interpolation ?? "previous";
      const chart = new uPlot(
        {
          width: Math.max(280, host.clientWidth),
          height: 150,
          title: key,
          scales: { x: { time: false } },
          series: [
            {},
            {
              label: String(declaration?.["unit"] ?? key),
              stroke: "#537461",
              spanGaps: false,
              paths:
                interpolation === "previous"
                  ? uPlot.paths.stepped!({ align: 1 })
                  : interpolation === "none"
                    ? () => null
                    : uPlot.paths.linear!(),
              points: { show: interpolation === "none" },
            },
          ],
          axes: [{}, {}],
        },
        [
          data.map((s) => s.cursor[0]),
          data.map((s) => (Number.isFinite(s.value) ? s.value : null)),
        ],
        host,
      );
      this.#plots.push(chart);
      const resize = new ResizeObserver(() => {
        if (host.clientWidth)
          chart.setSize({ width: host.clientWidth, height: 150 });
      });
      resize.observe(host);
      this.#chartResize.push(resize);
    }
    if (Object.keys(recording.scalars).length) {
      const final = el("details");
      final.append(el("summary", "Final recorded scalars"), el("div"));
      inspectValue(final.lastElementChild as HTMLElement, recording.scalars);
      this.#tiles.append(final);
    }
  }
  private updateKpis(frame: PreparedFrame): void {
    for (const [key, node] of this.#tileValues) {
      const value = frame.kpis[key];
      node.textContent =
        value?.value === null || value === undefined
          ? "—"
          : number(value.value);
      const source = node.nextElementSibling;
      if (source)
        source.textContent =
          value?.value !== null &&
          value?.sourceTime !== null &&
          value?.sourceTime !== undefined
            ? `${value.interpolated ? "Interpolated" : "Recorded"} · t = ${number(value.sourceTime)}`
            : "No eligible sample";
    }
    for (const plot of this.#plots)
      if (Number.isFinite(frame.time))
        plot.setCursor({ left: plot.valToPos(frame.time, "x"), top: 0 }, false);
  }
  async exportSource(limit: number, signal: AbortSignal): Promise<Blob> {
    const source = this.source;
    if (!source) throw new Error("Open a recording first");
    if (source.size > limit)
      throw new RangeError("Offline HTML supports traces up to 100 MiB");
    if (source instanceof Blob) return source;
    const parts: BlobPart[] = [];
    for (let offset = 0; offset < source.size; offset += 1024 * 1024) {
      signal.throwIfAborted();
      parts.push(
        new Blob([
          (await source.read(
            offset,
            Math.min(1024 * 1024, source.size - offset),
            signal,
          )) as Uint8Array<ArrayBuffer>,
        ]),
      );
    }
    return new Blob(parts);
  }
  focus(): void {
    if (this.#selected) this.scene?.focus(this.#selected);
  }
  dispose(): void {
    this.#generation++;
    this.#historyGeneration++;
    this.#chartGeneration++;
    this.#retiredGeneration++;
    this.#abort?.abort();
    this.playback?.dispose();
    this.scene?.dispose();
    this.#plots.forEach((p) => p.destroy());
    this.#chartResize.forEach((r) => r.disconnect());
    this.dock.dispose();
    this.#clean.forEach((fn) => fn());
  }
}
