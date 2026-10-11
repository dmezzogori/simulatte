export interface MotionProgress {
  fraction: number;
  approximate: boolean;
  diagnostic: string | null;
}
const clamp = (x: number) => Math.max(0, Math.min(1, x));
const map = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
/** Pure evaluation of recorded data. No model/profile callback is executed. */
export function motionProgress(value: unknown, time: number): MotionProgress {
  const m = map(value),
    start = m["t_start"],
    end = m["t_end"],
    d = map(m["description"]);
  const out = (
    fraction: number,
    approximate = false,
    diagnostic: string | null = null,
  ) => ({ fraction, approximate, diagnostic });
  if (
    typeof start !== "number" ||
    typeof end !== "number" ||
    Number.isNaN(start) ||
    Number.isNaN(end) ||
    end < start
  )
    return out(0, true, "Invalid motion timing");
  if (!Number.isFinite(end) || !Number.isFinite(start))
    return out(0, false, "Stalled");
  if (end === start) return out(time >= end ? 1 : 0);
  const elapsed = Math.max(0, Math.min(end - start, time - start)),
    fraction = clamp(elapsed / (end - start));
  const fallback = () => out(fraction, true, "Approximate motion");
  if (d["curve"] === "linear" || d["curve"] === "constant")
    return out(
      fraction,
      d["approximate"] === true,
      d["approximate"] === true ? "Approximate motion" : null,
    );
  if (d["curve"] === "keyframes") {
    const points = d["keyframes"],
      accuracy = d["accuracy"];
    if (
      !Array.isArray(points) ||
      points.length < 2 ||
      typeof accuracy !== "number" ||
      !Number.isFinite(accuracy) ||
      accuracy < 0
    )
      return fallback();
    let previous = -1,
      progress = -1;
    for (const p of points) {
      if (
        !Array.isArray(p) ||
        p.length !== 2 ||
        typeof p[0] !== "number" ||
        typeof p[1] !== "number" ||
        !Number.isFinite(p[0]) ||
        !Number.isFinite(p[1]) ||
        p[0] <= previous ||
        p[1] < progress ||
        p[1] < 0 ||
        p[1] > 1
      )
        return fallback();
      previous = p[0];
      progress = p[1];
    }
    const first = points[0] as number[],
      last = points.at(-1) as number[];
    const tolerance = Math.max(
      1e-12,
      8 *
        Math.pow(
          2,
          Math.floor(
            Math.log2(
              Math.max(Math.abs(start), Math.abs(end), Number.MIN_VALUE),
            ),
          ) - 52,
        ),
    );
    if (
      first[0] !== 0 ||
      first[1] !== 0 ||
      last[1] !== 1 ||
      Math.abs(last[0]! - (end - start)) > tolerance
    )
      return fallback();
    const t = fraction * last[0]!;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1] as number[],
        b = points[i] as number[];
      if (t <= b[0]!)
        return out(a[1]! + ((b[1]! - a[1]!) * (t - a[0]!)) / (b[0]! - a[0]!));
    }
    return out(1);
  }
  if (d["curve"] === "trapezoidal") {
    const speed = d["v_max"],
      accel = d["accel"],
      decel = d["decel"],
      distance = d["distance"];
    if (
      typeof distance !== "number" ||
      !Number.isFinite(distance) ||
      distance < 0
    )
      return fallback();
    if (distance === 0) return out(1);
    if (
      typeof speed !== "number" ||
      typeof accel !== "number" ||
      typeof decel !== "number" ||
      ![speed, accel, decel].every((x) => Number.isFinite(x) && x > 0)
    )
      return fallback();
    const peak = Math.min(
      speed,
      Math.sqrt((2 * distance * accel * decel) / (accel + decel)),
    );
    const ta = peak / accel,
      td = peak / decel,
      da = (peak * ta) / 2,
      dd = (peak * td) / 2,
      tc = Math.max(0, (distance - da - dd) / peak),
      duration = ta + tc + td;
    if (
      !Number.isFinite(duration) ||
      Math.abs(duration - (end - start)) >
        Math.max(
          1e-9,
          8 * Number.EPSILON * Math.max(Math.abs(start), Math.abs(end)),
        )
    )
      return fallback();
    const travelled =
      elapsed < ta
        ? (accel * elapsed * elapsed) / 2
        : elapsed < ta + tc
          ? da + peak * (elapsed - ta)
          : distance -
            (decel * Math.pow(Math.max(0, duration - elapsed), 2)) / 2;
    return out(clamp(travelled / distance));
  }
  return fallback();
}
