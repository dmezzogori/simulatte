import type { Graphics } from "pixi.js";
import type { MapNode } from "../map/model";
import { PALETTE } from "./palette";

/** Radius of a node in pixels. */
export const NODE_PX = 5;

/** A node of the network: a small circle of fixed pixel size, outlined more strongly when selected. */
export function drawNode(g: Graphics, node: MapNode, scale: number, selected: boolean): void {
  g.circle(node.x, node.y, NODE_PX / scale)
    .fill(PALETTE.node)
    .stroke({
      color: selected ? PALETTE.selected : PALETTE.nodeOutline,
      width: (selected ? 3 : 1) / scale,
    });
}
