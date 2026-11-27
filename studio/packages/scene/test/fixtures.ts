import { readFileSync } from "node:fs";
import type { PreparedFrame } from "@simulatte/playback";

/** The frame fixtures of the map, written by tests/fixtures/traces/generate_map_frames.py. */
const DIRECTORY = new URL("../../../fixtures/map/", import.meta.url);

/** `studio/fixtures/map/<name>.json` as the frame a viewer prepares at that cursor. */
export function loadFrame(name: string): PreparedFrame {
  const file = JSON.parse(readFileSync(new URL(`${name}.json`, DIRECTORY), "utf8")) as Pick<
    PreparedFrame,
    "time" | "cursor" | "state" | "initialState" | "layout"
  >;
  return {
    time: file.time,
    cursor: file.cursor,
    mode: "continuous",
    state: file.state,
    initialState: file.initialState ?? null,
    layout: file.layout,
    catalog: {},
    kpis: {},
    diagnostics: [],
    crossedEvents: [],
    crossingsComplete: true,
  };
}
