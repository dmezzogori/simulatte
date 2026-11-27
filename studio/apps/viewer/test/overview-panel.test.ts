import { describe, expect, it } from "vitest";
import { countsText, shapeOf, throttled } from "../src/overview-panel";

/** A stand-in for the DOM element `shapeOf` reads: its tag, class, line id and children. */
const node = (tag: string, cls: string, id: string | undefined, children: Element[] = []): Element =>
  ({ tagName: tag, className: cls, dataset: id === undefined ? {} : { id }, children }) as unknown as Element;

describe("the group heading", () => {
  it("names the counts as the lines do: a vehicle at rest on its way is stopped", () => {
    const counts = { stranded: 0, waiting: 1, moving: 2, working: 0, idle: 0, charging: 0, resting: 3, unknown: 0 };
    expect(countsText(counts)).toBe("1 waiting · 2 moving · 3 stopped");
  });
});

describe("shapeOf", () => {
  it("tells apart trees whose ids hold the punctuation of the shape", () => {
    const two = node("SECTION", "ov-section", undefined, [
      node("BUTTON", "ov-line", "x"),
      node("BUTTON", "ov-line", "y"),
    ]);
    const crafted = node("SECTION", "ov-section", undefined, [node("BUTTON", "ov-line", "x(),BUTTON.ov-line#y")]);
    expect(shapeOf(two)).not.toBe(shapeOf(crafted));
    expect(shapeOf(two)).toBe(
      shapeOf(node("SECTION", "ov-section", undefined, [node("BUTTON", "ov-line", "x"), node("BUTTON", "ov-line", "y")])),
    );
  });
});

describe("throttled", () => {
  it("throttles the panel whenever the status is unchanged and not paused", () => {
    expect(throttled("playing", false)).toBe(true);
    expect(throttled("buffering", false)).toBe(true);
    expect(throttled("ended", false)).toBe(true);
    expect(throttled("paused", false)).toBe(false);
  });
  it("flushes on a status change", () => {
    expect(throttled("playing", true)).toBe(false);
    expect(throttled("buffering", true)).toBe(false);
  });
});
