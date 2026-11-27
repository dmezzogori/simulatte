import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector } from "../src/project";
import type { VehicleItem } from "../src/map/model";
import { MAP_LABEL_CHARS, clipLabel, vehicleText } from "../src/draw/vehicle";
import { loadFrame } from "./fixtures";

const LONG = "x".repeat(500);
const vehicleIn = (frame: PreparedFrame, id: string): VehicleItem => {
  const map = new Projector(frame.initialState ?? frame.state).project(frame).map;
  const found = map?.vehicles.find((v) => v.id === id);
  if (!found) throw new Error(`no vehicle ${id}`);
  return found;
};
const edit = (frame: PreparedFrame, id: string, change: (fields: Record<string, unknown>) => Record<string, unknown>) => ({
  ...frame,
  state: { ...frame.state, [id]: change(frame.state[id]!) },
});
const length = (text: string): number => [...text].length;

describe("map labels", () => {
  it("clip to the clip length, the last character an ellipsis", () => {
    expect(clipLabel("AGV-1")).toBe("AGV-1");
    expect(length(clipLabel(LONG))).toBe(MAP_LABEL_CHARS);
    expect(clipLabel(LONG).endsWith("…")).toBe(true);
  });
  it("clip the whole line of a vehicle whose goal id is 500 characters", () => {
    const frame = edit(loadFrame("corridor-wait"), "AGV-2", (fields) => ({
      ...fields,
      route: { ...(fields["route"] as Record<string, unknown>), destination: LONG },
    }));
    const agv = vehicleIn(frame, "AGV-2");
    expect(agv.line).toContain(LONG);
    expect(length(vehicleText(agv, "true"))).toBeLessThanOrEqual(MAP_LABEL_CHARS);
  });
  it("clip the whole line of a vehicle whose blocker id is 500 characters", () => {
    const frame = edit(loadFrame("corridor-wait"), "AGV-1", (fields) => ({
      ...fields,
      wait: { ...(fields["wait"] as Record<string, unknown>), blockers: [LONG] },
    }));
    const agv = vehicleIn(frame, "AGV-1");
    expect(agv.line).toContain(LONG);
    expect(length(vehicleText(agv, "true"))).toBeLessThanOrEqual(MAP_LABEL_CHARS);
  });
  it("clip the short label: the trailing digits of an id can be long", () => {
    const agv = vehicleIn(loadFrame("corridor-wait"), "AGV-1");
    expect(vehicleText(agv, "symbol")).toBe("1");
    expect(length(vehicleText({ ...agv, short: "9".repeat(500) }, "symbol"))).toBe(MAP_LABEL_CHARS);
  });
});
