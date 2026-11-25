import type { Graphics, Text, TextStyleOptions } from "pixi.js";
import { PALETTE } from "./palette";

/** The longest the scale bar may be, in pixels. */
const MAX_PX = 150;
/** Distance of the bar from the left and bottom edges of the canvas, in pixels. */
const MARGIN = 16;
const TICK = 5;
/** Pixels: the bar's line and ticks, and the light halo under them on each side (the outline every sprite carries). */
const LINE_PX = 2;
const HALO_PX = 1.5;

/** The style of the bar's length: dark, with the same light outline, so that it reads on any export background. */
export const SCALE_BAR_TEXT = {
  fontFamily: "system-ui",
  fontSize: 12,
  fill: PALETTE.scaleBar,
  stroke: { color: PALETTE.outline, width: 2 * HALO_PX, join: "round" },
} as const satisfies TextStyleOptions;

/** The largest of 1, 2, 5 x 10^n metres whose length at `scale` pixels per metre is at most 150 px. */
export function scaleBar(scale: number): { metres: number; px: number } {
  const n = Math.floor(Math.log10(MAX_PX / scale));
  for (const step of [5, 2, 1]) {
    // Dividing by a power of ten keeps 0.2 exact where 2 * 0.1 would not be in general.
    const metres = n >= 0 ? step * 10 ** n : step / 10 ** -n;
    if (metres * scale <= MAX_PX * (1 + 1e-12)) return { metres, px: metres * scale };
  }
  // Rounding put the bound on the wrong side of a power of ten: the next one down fits.
  const metres = n - 1 >= 0 ? 5 * 10 ** (n - 1) : 5 / 10 ** (1 - n);
  return { metres, px: metres * scale };
}

/**
 * Draws the bar in screen pixels at the bottom-left of a canvas `height` pixels tall, its length above it. The line
 * and ticks are drawn twice: a wider light halo, then the dark bar over it.
 */
export function drawScaleBar(g: Graphics, text: Text, scale: number, height: number): void {
  const { metres, px } = scaleBar(scale),
    y = height - MARGIN;
  const bar = (target: Graphics): Graphics =>
    target
      .moveTo(MARGIN, y)
      .lineTo(MARGIN + px, y)
      .moveTo(MARGIN, y - TICK)
      .lineTo(MARGIN, y + TICK)
      .moveTo(MARGIN + px, y - TICK)
      .lineTo(MARGIN + px, y + TICK);
  bar(g.clear()).stroke({ color: PALETTE.outline, width: LINE_PX + 2 * HALO_PX, cap: "square" });
  bar(g).stroke({ color: PALETTE.scaleBar, width: LINE_PX });
  text.text = `${metres} m`;
  text.position.set(MARGIN + px / 2, y - TICK - 2);
}
