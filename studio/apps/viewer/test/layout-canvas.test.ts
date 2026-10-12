import {describe, expect, it} from 'vitest';
import {canvasBasisKey, canvasTargets, moveTargets, rotateTargets, snapDelta} from '../src/layout-canvas-model';
const bundle = {targets: {machine: {kind: 'server', explicitly_named: true}, 'server-1': {kind: 'server', explicitly_named: false}},
  resolved: {placements: {machine: {kind: 'server', position: [1, 2, 0], rotation: 90}, 'server-1': {kind: 'server', position: [0, 0, 0]}},
    network: {type: 'lanes', lanes: {main: {vertices: [{id: 'a', position: [0, 0, 0]}, {id: 'b', position: [2, 0, 0]}]}}}}};
describe('canvas draft transforms', () => {
  it('moves named placements with z=0 and preserves inherited kind and independent fields', () => {
    const layer = {schema_version: '1.0', placements: {machine: {kind: 'server', visual: {label: 'kept'}}}};
    const target = canvasTargets(layer, bundle).find(target => target.name === 'machine')!;
    const next = moveTargets(layer, [target], {x: 2, y: -1});
    expect(next.placements).toEqual({machine: {kind: 'server', visual: {label: 'kept'}, position: [3, 1, 0]}});
    expect(layer.placements.machine).not.toHaveProperty('position');
    expect(rotateTargets(next, [target], 300).placements).toEqual({machine: {kind: 'server', visual: {label: 'kept'}, position: [3, 1, 0], rotation: 30}});
  });
  it('keeps generated-id draft geometry for authoritative physical classification and warnings', () => {
    const layer = {schema_version: '1.0'};
    const targets = canvasTargets(layer, bundle).filter(target => target.type === 'placement');
    expect(targets.find(target => target.name === 'server-1')?.named).toBe(false);
    expect(moveTargets(layer, targets, {x: 1, y: 0}).placements).toHaveProperty('server-1');
    expect(layer).toEqual({schema_version: '1.0'});
  });
  it('copies inherited lane lists with stable vertex ids and leaves stale segment overrides untouched', () => {
    const layer = {schema_version: '1.0', overrides: [{id: 'old-segment', target: {kind: 'lane_segment', lane: 'main', source: 'a', target: 'b'}, properties: {enabled: false}}]};
    const target = canvasTargets(layer, bundle).find(target => target.name === 'main / a')!;
    const next = moveTargets(layer, [target], {x: 1, y: 2});
    expect(next.network).toEqual({type: 'lanes', lanes: {main: {vertices: [{id: 'a', position: [1, 2, 0]}, {id: 'b', position: [2, 0, 0]}]}}});
    expect(next.overrides).toEqual(layer.overrides);
  });
  it('snaps group movement relative to the first target and the declared origin', () => {
    expect(snapDelta({x: 1.2, y: 2.4}, {x: 1, y: 2}, {dx: 2, dy: 3, origin: [1, 1, 0]})).toEqual({x: 2, y: 2});
  });
});

it('keeps the effective canvas basis stable when a completed run publishes the already edited geometry', () => {
  const layer = {schema_version: '1.0', placements: {machine: {position: [3, 2, 0]}}};
  const published = structuredClone(bundle);
  published.resolved.placements.machine.position = [3, 2, 0];
  expect(canvasBasisKey(layer, published)).toBe(canvasBasisKey(layer, bundle));
  published.resolved.placements.machine.rotation = 180;
  expect(canvasBasisKey(layer, published)).not.toBe(canvasBasisKey(layer, bundle));
  published.resolved.placements.machine.rotation = 90;
  published.targets.machine.explicitly_named = false;
  expect(canvasBasisKey(layer, published)).not.toBe(canvasBasisKey(layer, bundle));
});
