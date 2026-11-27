import { describe, expect, it } from "vitest";
import type { Graphics, Text } from "pixi.js";
import { SCALE_BAR_TEXT, drawScaleBar, scaleBar } from "../src/draw/scale-bar";
import { PALETTE } from "../src/draw/palette";

describe("scaleBar", () => {
  it("takes the largest of 1, 2, 5 x 10^n metres that fits in 150 px", () => {
    expect(scaleBar(2)).toEqual({ metres: 50, px: 100 });
    expect(scaleBar(11)).toEqual({ metres: 10, px: 110 });
    expect(scaleBar(30)).toEqual({ metres: 5, px: 150 });
    expect(scaleBar(400).metres).toBe(0.2);
    expect(scaleBar(400).px).toBeCloseTo(80, 9);
  });
  it("never exceeds 150 px and is never shorter than 60 px", () => {
    for (let scale = 2; scale <= 400; scale *= 1.07) {
      const { px } = scaleBar(scale);
      expect(px).toBeLessThanOrEqual(150 + 1e-9);
      expect(px).toBeGreaterThan(150 / 2.5 - 1e-9);
    }
  });
});

/** A stand-in for a Pixi `Graphics` that records the strokes and the number of segments each one draws. */
function recorder(): { g: Graphics; strokes: { color: number; width: number; segments: number }[] } {
  const strokes: { color: number; width: number; segments: number }[] = [];
  let segments = 0;
  const g = {
    clear: () => g,
    moveTo: () => g,
    lineTo: () => {
      segments++;
      return g;
    },
    stroke: (style: { color: number; width: number }) => {
      strokes.push({ color: style.color, width: style.width, segments });
      segments = 0;
      return g;
    },
  };
  return { g: g as unknown as Graphics, strokes };
}

describe("drawScaleBar", () => {
  it("draws the bar and its ticks over a wider light halo, so that it shows on a dark background", () => {
    const { g, strokes } = recorder();
    const text = { text: "", position: { set: () => {} }, style: {} } as unknown as Text;
    drawScaleBar(g, text, 30, 500);
    expect(strokes.map((s) => s.color)).toEqual([PALETTE.outline, PALETTE.scaleBar]);
    expect(strokes[0]!.segments).toBe(strokes[1]!.segments);
    expect(strokes[0]!.width).toBeGreaterThan(strokes[1]!.width);
  });
  it("gives its length text the same light outline", () => {
    expect(SCALE_BAR_TEXT.stroke).toMatchObject({ color: PALETTE.outline });
    expect(SCALE_BAR_TEXT.fill).toBe(PALETTE.scaleBar);
  });
});
