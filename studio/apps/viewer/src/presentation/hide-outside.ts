/** Presentation mode hides the rest of the document while it is shown: at every level from the mode up to
 * ``root``, the siblings become inert and aria-hidden, so neither focus nor assistive technology reaches the
 * workspace behind it. The mode stays in place, inside the workspace's styles. */
export interface Hideable {
  inert: boolean;
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}
export interface TreeNode extends Hideable {
  parentElement: TreeNode | null;
  children: ArrayLike<TreeNode>;
}
export function hideOutside(keep: TreeNode, root: TreeNode): () => void {
  const hidden: { element: Hideable; inert: boolean; ariaHidden: string | null }[] = [];
  for (let node = keep; node !== root && node.parentElement; node = node.parentElement) {
    for (const element of Array.from(node.parentElement.children)) {
      if (element === node) continue;
      hidden.push({ element, inert: element.inert, ariaHidden: element.getAttribute("aria-hidden") });
      element.inert = true;
      element.setAttribute("aria-hidden", "true");
    }
  }
  return () => {
    for (const { element, inert, ariaHidden } of hidden) {
      element.inert = inert;
      if (ariaHidden === null) element.removeAttribute("aria-hidden");
      else element.setAttribute("aria-hidden", ariaHidden);
    }
  };
}
