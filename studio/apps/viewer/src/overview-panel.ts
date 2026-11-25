import type { Overview, OverviewLine, OverviewOptions } from "@simulatte/scene";

// The Overview panel's DOM (spec section 5.6). It uses `createElement` and `textContent` only: a label from a
// trace never reaches the page as markup. Nothing here depends on React.

const STATES = ["stranded", "waiting", "moving", "working", "idle", "charging", "resting", "unknown"] as const;

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};

/** What a root last showed: the content signature and the line marked as selected. */
interface Shown {
  signature: string;
  current: string | null;
}
const shown = new WeakMap<HTMLElement, Shown>();

function line(entry: OverviewLine, current: string | null, onSelect: (id: string) => void, root: HTMLElement): HTMLElement {
  const button = el("button", undefined, "ov-line");
  button.type = "button";
  button.dataset["id"] = entry.id;
  if (entry.shown) button.dataset["shown"] = entry.shown;
  if (entry.id === current) button.setAttribute("aria-current", "true");
  button.append(el("span", undefined, "ov-mark"), el("span", entry.text, "ov-text"));
  button.title = entry.text;
  button.onclick = () => {
    const state = shown.get(root);
    if (state) state.current = entry.id;
    for (const other of Array.from(root.querySelectorAll(".ov-line[aria-current]"))) other.removeAttribute("aria-current");
    button.setAttribute("aria-current", "true");
    onSelect(entry.id);
  };
  return button;
}

function more(hidden: number): HTMLElement | null {
  return hidden > 0 ? el("p", `${hidden} more not shown. Narrow the list with the filter.`, "ov-more") : null;
}

function section(title: string): HTMLElement {
  const node = el("section", undefined, "ov-section");
  node.append(el("h3", title));
  return node;
}

/**
 * Writes the panel into `root`. A value equal to the one shown leaves the DOM alone, so a focused line, the scroll
 * position and a text selection survive a refresh. `selected` marks the line of the selected element; without it
 * the mark follows the last click.
 */
export function renderOverview(
  root: HTMLElement,
  value: Overview | null,
  onSelect: (id: string) => void,
  selected?: string | null,
): void {
  const before = shown.get(root);
  const current = selected !== undefined ? selected : (before?.current ?? null);
  const signature = JSON.stringify(value);
  if (before && before.signature === signature) {
    if (before.current !== current) {
      for (const node of Array.from(root.querySelectorAll<HTMLElement>(".ov-line")))
        if (node.dataset["id"] === current) node.setAttribute("aria-current", "true");
        else node.removeAttribute("aria-current");
      before.current = current;
    }
    return;
  }
  shown.set(root, { signature, current });
  const focused = root.contains(document.activeElement) ? (document.activeElement as HTMLElement) : null;
  const focusId = focused?.classList.contains("ov-line") ? (focused.dataset["id"] ?? null) : null;
  const scroll = root.scrollTop;
  if (value === null) {
    root.replaceChildren(el("p", "No map for this trace", "ov-empty"));
    return;
  }
  const children: HTMLElement[] = [];
  const vehicles = section("Vehicles");
  if (!value.vehicles.length) vehicles.append(el("p", "No vehicles", "ov-empty"));
  for (const group of value.vehicles) {
    const counts = STATES.filter((s) => group.counts[s] > 0).map((s) => `${group.counts[s]} ${s}`);
    const heading = el("h4", undefined, "ov-group");
    heading.append(el("span", group.fleet || "No fleet", "ov-fleet"), el("span", counts.join(" · "), "ov-counts"));
    vehicles.append(heading);
    if (!group.lines.length && !group.hidden) vehicles.append(el("p", "No vehicle matches", "ov-empty"));
    for (const entry of group.lines) vehicles.append(line(entry, current, onSelect, root));
    const rest = more(group.hidden);
    if (rest) vehicles.append(rest);
  }
  const goods = section("Goods");
  const statuses = Object.entries(value.goods.counts).map(([status, n]) => `${n} ${status.toLowerCase().replaceAll("_", " ")}`);
  goods.append(el("p", statuses.length ? statuses.join(" · ") : "No open orders", "ov-counts"));
  for (const entry of value.goods.lines) goods.append(line(entry, current, onSelect, root));
  const rest = more(value.goods.hidden);
  if (rest) goods.append(rest);
  const traffic = section("Traffic");
  traffic.append(el("p", value.traffic));
  const facilities = section("Facilities");
  if (!value.facilities.length) facilities.append(el("p", "No facilities", "ov-empty"));
  for (const entry of value.facilities) facilities.append(line(entry, current, onSelect, root));
  children.push(vehicles, goods, traffic, facilities);
  root.replaceChildren(...children);
  root.scrollTop = scroll;
  if (focusId !== null)
    for (const node of Array.from(root.querySelectorAll<HTMLElement>(".ov-line")))
      if (node.dataset["id"] === focusId) {
        node.focus({ preventScroll: true });
        break;
      }
}

/**
 * The panel with its own controls: a text filter and a "Waiting only" switch. The controls are built once, so they
 * keep their value and focus while `body` is rewritten; `onChange` asks for a refresh.
 */
export function createOverviewPanel(onChange: () => void): {
  element: HTMLElement;
  body: HTMLElement;
  options: () => OverviewOptions;
} {
  const element = el("div", undefined, "overview");
  const controls = el("div", undefined, "ov-controls");
  const filter = el("input");
  filter.type = "search";
  filter.placeholder = "Filter vehicles";
  filter.setAttribute("aria-label", "Filter vehicles");
  const label = el("label", undefined, "ov-switch");
  const waiting = el("input");
  waiting.type = "checkbox";
  waiting.setAttribute("aria-label", "Waiting only");
  label.append(waiting, el("span", "Waiting only"));
  filter.oninput = waiting.onchange = onChange;
  controls.append(filter, label);
  const body = el("div", undefined, "ov-body");
  element.append(controls, body);
  return { element, body, options: () => ({ filter: filter.value, waitingOnly: waiting.checked }) };
}
