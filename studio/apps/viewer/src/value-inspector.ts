/** Bounded tree inspection: no recursive serialization of untrusted recorded payloads. */
const states = new WeakMap<
  HTMLElement,
  { value: unknown; path: string[]; page: number; render(): void }
>();
export function inspectValue(host: HTMLElement, value: unknown): void {
  const existing = states.get(host);
  if (existing) {
    if (existing.value === value) return;
    existing.value = value;
    existing.render();
    return;
  }
  const state = { value, path: [] as string[], page: 0, render: () => {} };
  state.render = () => {
    let at = state.value;
    for (const key of state.path) {
      if (at && typeof at === "object" && Object.hasOwn(at, key))
        at = (at as Record<string, unknown>)[key];
      else {
        state.path = [];
        at = state.value;
        break;
      }
    }
    const nodes: HTMLElement[] = [];
    const button = (label: string, action: () => void) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.onclick = action;
      return b;
    };
    if (state.path.length)
      nodes.push(
        button("↑ " + state.path.join(" / "), () => {
          state.path.pop();
          state.page = 0;
          state.render();
        }),
      );
    const keys = at && typeof at === "object" ? Object.keys(at) : null;
    const scalar = (v: unknown): string =>
      v === undefined
        ? "—"
        : typeof v === "number" && !Number.isFinite(v)
          ? Number.isNaN(v)
            ? "NaN"
            : v > 0
              ? "+∞"
              : "−∞"
          : typeof v === "string"
            ? v
            : String(v);
    const length = keys ? keys.length : scalar(at).length;
    const size = keys ? 200 : 4096;
    state.page = Math.min(
      state.page,
      Math.max(0, Math.ceil(length / size) - 1),
    );
    if (keys) {
      for (const key of keys.slice(
        state.page * size,
        (state.page + 1) * size,
      )) {
        const child = (at as Record<string, unknown>)[key];
        const complex = child !== null && typeof child === "object";
        const text = complex
          ? `${Array.isArray(child) ? "Array" : "Object"} (${Object.keys(child).length})`
          : scalar(child);
        const row = document.createElement("div");
        row.className = "field-row";
        const name = document.createElement("strong");
        name.textContent = key.slice(0, 512);
        row.append(name);
        if (complex || text.length > 512)
          row.append(
            button(complex ? text : text.slice(0, 120) + "…", () => {
              state.path.push(key);
              state.page = 0;
              state.render();
            }),
          );
        else {
          const content = document.createElement("span");
          content.textContent = text;
          row.append(content);
        }
        nodes.push(row);
      }
    } else {
      const pre = document.createElement("pre");
      pre.textContent = scalar(at).slice(
        state.page * size,
        (state.page + 1) * size,
      );
      nodes.push(pre);
    }
    if (length > size) {
      const nav = document.createElement("div");
      const before = button("Previous values", () => {
        state.page--;
        state.render();
      });
      before.disabled = state.page === 0;
      const after = button("Next values", () => {
        state.page++;
        state.render();
      });
      after.disabled = (state.page + 1) * size >= length;
      nav.append(
        before,
        document.createTextNode(
          ` ${state.page + 1} / ${Math.ceil(length / size)} `,
        ),
        after,
      );
      nodes.push(nav);
    }
    host.replaceChildren(...nodes);
  };
  states.set(host, state);
  state.render();
}
export function clearInspection(host: HTMLElement): void {
  states.delete(host);
  host.replaceChildren();
}
