import type { DetailLine } from "./describe";

// The DOM of the click detail (spec section 5.7). It uses `createElement` and `textContent` only: a label from a
// trace never reaches the page as markup. Nothing here depends on React, so the viewer and the offline player
// share it.

/** What a root last showed: the content signature and the latest handler (the buttons call it, not the first). */
interface Shown {
  signature: string;
  onSelect: (id: string) => void;
}
const shown = new WeakMap<HTMLElement, Shown>();

/**
 * Writes the lines into `root`, one block each; the text first, then a button for every id the line names. A value
 * equal to the one shown leaves the DOM alone, and a rewrite gives the focus back to the same button.
 */
export function renderDetail(
  root: HTMLElement,
  lines: readonly DetailLine[],
  onSelect: (id: string) => void,
): void {
  const before = shown.get(root);
  const signature = JSON.stringify(lines);
  if (before) {
    before.onSelect = onSelect;
    if (before.signature === signature && root.firstChild) return;
  }
  const current: Shown = before ?? { signature, onSelect };
  current.signature = signature;
  current.onSelect = onSelect;
  shown.set(root, current);
  const focused = root.contains(document.activeElement) ? (document.activeElement as HTMLElement) : null;
  const focus = focused?.classList.contains("detail-ref") ? [focused.dataset["line"], focused.dataset["ref"]] : null;
  const blocks = lines.map((line, index) => {
    const block = document.createElement("div");
    block.className = "detail-line";
    const text = document.createElement("span");
    text.className = "detail-text";
    text.textContent = line.text;
    block.append(text);
    for (const ref of line.refs ?? []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "detail-ref";
      button.dataset["line"] = String(index);
      button.dataset["ref"] = ref;
      button.textContent = ref;
      button.onclick = () => current.onSelect(ref);
      block.append(" ", button);
    }
    return block;
  });
  root.replaceChildren(...blocks);
  if (focus)
    for (const button of Array.from(root.querySelectorAll<HTMLElement>(".detail-ref")))
      if (button.dataset["line"] === focus[0] && button.dataset["ref"] === focus[1]) {
        button.focus({ preventScroll: true });
        break;
      }
}
