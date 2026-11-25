import { describe, expect, it } from "vitest";
import { scaleBar } from "../src/draw/scale-bar";

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
