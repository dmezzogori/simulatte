import {layoutOf, type LayoutBundle, type RecordingView} from '@simulatte/trace';

const pinnedLayouts = new WeakSet<object>();

/** Copy data-only presentation metadata so later edits cannot change a pinned view/export. */
export function freezePresentation(layout: LayoutBundle): LayoutBundle {
  if (pinnedLayouts.has(layout)) return layout;
  const ancestors = new Set<object>();
  const copy = (value: unknown, depth: number): unknown => {
    if (depth > 64) throw new TypeError('Presentation layout depth exceeds 64');
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value !== 'object' || value === null) throw new TypeError('Presentation layout must be finite data');
    if (ancestors.has(value)) throw new TypeError('Presentation layout contains a cycle');
    ancestors.add(value);
    try {
      if (Array.isArray(value)) return Object.freeze(value.map(item => copy(item, depth + 1)));
      if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)
        throw new TypeError('Presentation layout must contain plain maps');
      const result: Record<string, unknown> = Object.create(null);
      for (const [key, item] of Object.entries(value)) result[key] = copy(item, depth + 1);
      return Object.freeze(result);
    } finally {ancestors.delete(value);}
  };
  if (layout.schema_version !== '1.0') throw new TypeError('Unsupported presentation layout schema');
  const result = layoutOf(copy(layout, 0));
  pinnedLayouts.add(result);
  return result;
}
export function pinPresentation(recording: RecordingView, layout: LayoutBundle | null): LayoutBundle | null {
  if (layout === null) return null;
  const original = recording.trace.layout;
  if (!original || original.schema_version !== '1.0') throw new TypeError('Presentation requires a supported recorded layout');
  const pinned = freezePresentation(layout);
  if (pinned.physical_hash !== original.physical_hash) throw new TypeError('Presentation layout has a different physical hash');
  return pinned;
}
