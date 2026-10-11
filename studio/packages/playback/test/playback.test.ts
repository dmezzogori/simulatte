import { expect, it } from "vitest";
import { openRecording, type Sample } from "@simulatte/trace";
import { buildTrace } from "../../trace/test/build";
import {
  kpiValue,
  Playback,
  Session,
  type Clock,
  type PreparedFrame,
} from "../src/index";
const sample = (time: number, seq: number, value: number): Sample => ({
  cursor: [time, seq],
  value,
  key: "v",
});
it("exact KPI inspection never interpolates using a future sample", () => {
  const left = sample(0, 1, 10),
    right = sample(2, 3, 20),
    display = { interpolation: "linear", missing: "gap" } as const;
  expect(
    kpiValue(
      { mode: "exact", time: 1, cursor: [1, 2] },
      display,
      left,
      right,
      left,
    ),
  ).toEqual({ value: 10, sourceTime: 0, interpolated: false });
  expect(
    kpiValue(
      { mode: "continuous", time: 1, cursor: [0, 1] },
      display,
      left,
      right,
      left,
    ),
  ).toEqual({ value: 15, sourceTime: 1, interpolated: true });
  expect(
    kpiValue(
      { mode: "continuous", time: 3, cursor: [2, 3] },
      display,
      right,
      null,
      right,
    ).value,
  ).toBeNull();
  expect(
    kpiValue(
      { mode: "activation", time: 0, cursor: [0, -1] },
      display,
      left,
      right,
      left,
    ).value,
  ).toBeNull();
  expect(
    kpiValue(
      { mode: "continuous", time: 1, cursor: [1, 2] },
      { interpolation: "previous", missing: "hold" },
      sample(1, 2, NaN),
      right,
      left,
    ).value,
  ).toBe(10);
});
it("steps every same-time event and freezes observer state at preceding domain event", async () => {
  const initial = {
    state: { cell: { $kind: "cell", v: 0 } },
    cursor: [0, -1],
    manifest: {},
  };
  const r = await openRecording(
    buildTrace(
      initial,
      [
        {
          snapshot: initial.state,
          events: [
            [0, 0, "tick", 0, {}, [["set", "cell", "v", 1]]],
            [2, 1, "tick", 0, {}, [["set", "cell", "v", 2]]],
          ],
        },
      ],
      {
        records: [
          [
            4,
            { epoch: 1, types: { log: { category: "observer" } }, kinds: {} },
          ],
          [
            9,
            {
              phase: "simulation",
              first: [0, 1],
              last: [0, 1],
              epoch: 1,
              events: [[1, null, "log", 0, {}, []]],
            },
          ],
        ],
      },
    ),
  );
  const clock: Clock = { request: () => 0, cancel: () => {} };
  const p = new Playback(new Session(r), clock);
  await p.seek({ mode: "activation", time: 0, cursor: null });
  expect(p.frame?.state["cell"]?.["v"]).toBe(0);
  await p.step(1);
  expect(p.frame?.state["cell"]?.["v"]).toBe(1);
  await p.step(1);
  expect(p.frame?.cursor).toEqual([0, 1]);
  expect(p.frame?.state["cell"]?.["v"]).toBe(1);
  await p.step(1);
  expect(p.frame?.state["cell"]?.["v"]).toBe(2);
  await p.step(-1);
  expect(p.frame?.state["cell"]?.["v"]).toBe(1);
  p.dispose();
});
it("late seek completion cannot replace a more recent frame", async () => {
  const pending: ((frame: PreparedFrame) => void)[] = [];
  const session = {
    prepare: () =>
      new Promise<PreparedFrame>((resolve) => pending.push(resolve)),
    dispose: () => {},
  } as unknown as Session;
  const p = new Playback(session, { request: () => 0, cancel: () => {} });
  const a = p.seek({ mode: "exact", time: 1, cursor: [1, 1] }),
    b = p.seek({ mode: "exact", time: 2, cursor: [2, 2] });
  const frame = {
    mode: "exact",
    time: 2,
    cursor: [2, 2],
    state: {},
    layout: null,
    catalog: {},
    kpis: {},
    diagnostics: [],
  } as PreparedFrame;
  pending[1]!(frame);
  await b;
  pending[0]!({ ...frame, time: 1 });
  await a;
  expect(p.frame?.time).toBe(2);
  p.dispose();
});

it("uses the last duplicate-time sample for continuous interpolation but preserves exact sequence eligibility", async () => {
  const { readFileSync } = await import("node:fs");
  const bytes = readFileSync(
    new URL(
      "../../../../tests/fixtures/traces/viewer/viewer.simtrace",
      import.meta.url,
    ),
  );
  const session = new Session(
    await openRecording(Uint8Array.from(bytes).buffer),
  );
  session.setSeries(["first/level"]);
  expect(
    (await session.prepare({ mode: "continuous", time: 2.5, cursor: null }))
      .kpis["first/level"]?.value,
  ).toBe(1.5);
  expect(
    (await session.prepare({ mode: "exact", time: 5, cursor: [5, 8] })).kpis[
      "first/level"
    ],
  ).toMatchObject({ value: 2, interpolated: false });
  expect(
    (await session.prepare({ mode: "exact", time: 5, cursor: [5, 9] })).kpis[
      "first/level"
    ],
  ).toMatchObject({ value: 3, interpolated: false });
  expect(
    (await session.prepare({ mode: "continuous", time: 10, cursor: null }))
      .kpis["first/level"]?.value,
  ).toBe(3);
  session.dispose();
});

it("discards a late event-step lookup after a newer seek", async () => {
  let complete!: (events: unknown[]) => void;
  const frame = {
    mode: "exact",
    time: 1,
    cursor: [1, 1],
    state: {},
    layout: null,
    catalog: {},
    kpis: {},
    diagnostics: [],
  } as PreparedFrame;
  const session = {
    prepare: async (position: unknown) => ({
      ...frame,
      ...(position as object),
    }),
    recording: {
      events: () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    },
    dispose: () => {},
  } as unknown as Session;
  const playback = new Playback(session, {
    request: () => 0,
    cancel: () => {},
  });
  await playback.seek({ mode: "exact", time: 1, cursor: [1, 1] });
  const step = playback.step(1);
  await playback.seek({ mode: "exact", time: 4, cursor: [4, 4] });
  complete([{ t: 2, seq: 2 }]);
  await step;
  expect(playback.frame?.time).toBe(4);
  playback.dispose();
});

it("plays remaining equal-time events in order before advancing the clock and resets after hiding", async () => {
  const initial = {state: {cell: {$kind: "cell", v: 0}}, cursor: [0,-1], manifest: {}};
  const r = await openRecording(buildTrace(initial,[{snapshot: initial.state,events:[
    [0,0,"tick",0,{},[["set","cell","v",1]]],
    [1,1,"tick",0,{},[["set","cell","v",2]]],
    [2,2,"tick",10,{},[["set","cell","v",3]]],
  ]}]));
  let now = 0, callback: ((n:number)=>void)|null = null;
  const p = new Playback(new Session(r), {request: cb => {callback=cb;return 1;}, cancel:()=>{callback=null;},now:()=>now});
  await p.seek({mode:"activation",time:0,cursor:[0,-1]});
  const tick = async (n:number) => {
    now+=n;const cb=callback;callback=null;cb?.(now);
    for(let i=0;i<100&&!callback&&p.status!=="ended";i++) await new Promise(resolve=>setTimeout(resolve,1));
  };
  p.play();await tick(0);await tick(16);
  expect(p.frame?.cursor).toEqual([0,0]);
  await tick(16);expect(p.frame?.cursor).toEqual([0,1]);
  p.setHidden(true);await tick(100000);expect(p.frame?.time).toBe(0);
  p.setHidden(false);await tick(16);expect(p.frame?.time).toBe(0);
  await tick(16);expect(p.frame?.time).toBeCloseTo(.016);
  p.dispose();expect(callback).toBeNull();
});

it.each(["previous","linear","none"] as const)("applies %s missing/hold and exact-time eligibility", interpolation=>{
 const left=sample(10,20,NaN),finite=sample(0,1,4),right=sample(20,30,12);
 const at={mode:"continuous",time:15,cursor:[10,20]} as const;
 expect(kpiValue(at,{interpolation,missing:"gap"},left,right,finite).value).toBeNull();
 expect(kpiValue(at,{interpolation,missing:"hold"},left,right,finite).value).toBe(interpolation==="linear"?8:interpolation==="previous"?4:null);
 const exact={mode:"exact",time:10,cursor:[10,21]} as const;
 expect(kpiValue(exact,{interpolation,missing:"hold"},left,right,finite)).toMatchObject({value:4,interpolated:false});
 expect(kpiValue(at,{interpolation,missing:"hold"},null,right,null).value).toBeNull();
});
