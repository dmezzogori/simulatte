import {useEffect, useMemo, useRef, useState} from 'react';
import {Scene, type Projection} from '@simulatte/scene';
import {asObject, canvasBasisKey, canvasTargets, moveTargets, rotateTargets, snapDelta, type CanvasTarget, type XY} from './layout-canvas-model';
import type {Layer} from './layout-history';
function projection(targets: CanvasTarget[], selected: Set<string>, delta: XY = {x: 0, y: 0}): Projection {
  return {fallback: false, links: [], ports: [], glyphs: targets.map(target => ({
    id: target.id, kind: target.kind, label: target.name, x: target.x + (selected.has(target.id) ? delta.x : 0),
    y: target.y + (selected.has(target.id) ? delta.y : 0), width: target.width, depth: target.depth,
    rotation: target.rotation * Math.PI / 180, status: '', color: selected.has(target.id) ? 0xea8f25 : target.named ? 0x729675 : 0xaaaaaa,
    queue: 0, unplaced: false, diagnostic: null, badge: '', shape: target.type === 'placement' || target.type === 'blocked' ? 'rectangle' : 'circle',
  }))};
}
/** Pointer motion draws imperatively; only pointer-up commits one draft gesture. */
export function LayoutCanvas({layer, bundle, enabled, change, onError}: {layer: Layer; bundle: Layer; enabled: boolean; change(layer: Layer): void; onError(message: string): void}) {
  const host = useRef<HTMLDivElement>(null), scene = useRef<Scene | null>(null);
  const selected = useRef(new Set<string>());
  const [selection, setSelection] = useState<string[]>([]), [snap, setSnap] = useState(true), [ready, setReady] = useState(false);
  const [filter, setFilter] = useState(''), [page, setPage] = useState(0);
  const [dx, setDx] = useState('0'), [dy, setDy] = useState('0'), [angle, setAngle] = useState('90');
  const basisKey = useMemo(() => canvasBasisKey(layer, bundle), [layer, bundle]);
  const basis = useMemo(() => JSON.parse(basisKey) as Layer, [basisKey]);
  const targets = useMemo(() => canvasTargets({}, basis), [basis]);
  const filtered = targets.filter(target => target.name.toLowerCase().includes(filter.toLowerCase()));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 100) - 1));
  const visible = filtered.slice(currentPage * 100, (currentPage + 1) * 100);
  const gesture = useRef<{start: XY; targets: CanvasTarget[]; delta: XY; moved: boolean} | null>(null);
  useEffect(() => {
    if (!host.current) return;
    const current = new Scene(); scene.current = current; let closed = false;
    current.onError = onError;
    void current.init(host.current).then(() => {if (!closed) setReady(true);}).catch(error => {if (!closed) onError(String(error));});
    return () => {closed = true; current.dispose(); scene.current = null;};
  }, []);
  useEffect(() => {
    const current = scene.current; if (!current || !ready) return;
    gesture.current = null;
    const selection = new Set([...selected.current].filter(id => targets.some(target => target.id === id)));
    selected.current = selection; setSelection([...selection]);
    current.renderProjection(projection(targets, selection));
    if (targets.length && current.camera.x === 0 && current.camera.y === 0) current.fit();
    current.onEditorPointer = event => {
      if (!enabled) return false;
      if (event.phase === 'start') {
        if (!event.id) {selected.current.clear(); setSelection([]); current.renderProjection(projection(targets, selected.current)); return false;}
        if (event.shift) {
          if (selected.current.has(event.id)) selected.current.delete(event.id); else selected.current.add(event.id);
        } else if (!selected.current.has(event.id)) selected.current = new Set([event.id]);
        const chosen = targets.filter(target => selected.current.has(target.id));
        setSelection([...selected.current]); current.renderProjection(projection(targets, selected.current));
        gesture.current = {start: event.point, targets: chosen, delta: {x: 0, y: 0}, moved: false};
        return true;
      }
      const active = gesture.current; if (!active) return false;
      if (event.phase === 'cancel') {gesture.current = null; current.renderProjection(projection(targets, selected.current)); return true;}
      let delta = {x: event.point.x - active.start.x, y: event.point.y - active.start.y};
      if (Math.hypot(delta.x, delta.y) * current.camera.scale > 3) active.moved = true;
      if (!active.moved) delta = {x: 0, y: 0};
      if (active.moved && snap && active.targets[0]) delta = snapDelta(delta, active.targets[0], {...asObject(asObject(bundle.resolved).grid), ...asObject(asObject(basis.resolved).grid)});
      active.delta = delta;
      if (event.phase === 'move') current.renderProjection(projection(targets, selected.current, delta));
      else {
        gesture.current = null;
        try {if (delta.x || delta.y) change(moveTargets(layer, active.targets, delta));}
        catch (error) {onError(String(error));}
        current.renderProjection(projection(targets, selected.current));
      }
      return true;
    };
    return () => {current.onEditorPointer = null; gesture.current = null;};
  }, [targets, layer, basis, enabled, snap, ready]);
  const transform = (rotate: boolean) => {
    const chosen = targets.filter(target => selected.current.has(target.id));
    try {
      const raw = rotate ? [angle] : [dx, dy];
      if (raw.some(value => !/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(value))) throw new Error('Enter finite numeric offsets');
      change(rotate ? rotateTargets(layer, chosen, Number(angle)) : moveTargets(layer, chosen, {x: Number(dx), y: Number(dy)}));
    } catch (error) {onError(String(error));}
  };
  return <section aria-label="Layout canvas">
    <p>Draft geometry. Shift-click selects multiple handles; drag to move. Physical graph validation is separate.</p>
    <div className="layout-canvas" ref={host} />
    <button type="button" onClick={() => scene.current?.fit()}>Fit layout</button>
    <label><input type="checkbox" checked={snap} onChange={event => setSnap(event.target.checked)} />Snap to declared grid</label>
    <label>Find handle<input value={filter} onChange={event => {setFilter(event.target.value); setPage(0);}} /></label>
    <label>Selected handles<select multiple value={selection} onChange={event => {
      selected.current = new Set([...selected.current].filter(id => !visible.some(target => target.id === id))
        .concat(Array.from(event.target.selectedOptions, option => option.value)));
      setSelection([...selected.current]); scene.current?.renderProjection(projection(targets, selected.current));
    }}>{visible.map(target => <option key={target.id} value={target.id}>{target.name}{target.named ? '' : ' (generated id)'}</option>)}</select></label>
    <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous handles</button>
    <span>{filtered.length ? currentPage * 100 + 1 : 0}–{Math.min(filtered.length, (currentPage + 1) * 100)} of {filtered.length} · {selection.length} selected</span>
    <button type="button" disabled={(currentPage + 1) * 100 >= filtered.length} onClick={() => setPage(currentPage + 1)}>Next handles</button>
    <button type="button" onClick={() => {selected.current.clear(); setSelection([]); scene.current?.renderProjection(projection(targets, selected.current));}}>Clear handle selection</button>
    <fieldset disabled={!enabled || selection.length === 0}>
      {targets.some(target => selected.current.has(target.id) && !target.named) && <p role="alert">Generated ids selected: presentation edits carry a stability warning. Python refuses physical overrides; name the entity in code for physical editing.</p>}
      <label>Move x<input inputMode="decimal" value={dx} onChange={event => setDx(event.target.value)} /></label>
      <label>Move y<input inputMode="decimal" value={dy} onChange={event => setDy(event.target.value)} /></label>
      <button type="button" onClick={() => transform(false)}>Move selected</button>
      <label>Rotation in degrees<input inputMode="decimal" value={angle} onChange={event => setAngle(event.target.value)} /></label>
      <button type="button" onClick={() => transform(true)}>Rotate selected</button>
    </fieldset>
  </section>;
}
