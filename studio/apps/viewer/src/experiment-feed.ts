/** Coalesced experiment notifications. Progress lands in plain fields at notification rate;
 * subscribers (React) get a new immutable snapshot at most once per interval. */
import type {Notification, Reference, WorkerPool} from './studio-client';

export interface RunProgress {run_id: string; status: string; counts: Record<string, number>; configurations: Record<string, number>[];}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

/** What a render reads; a new identity per flush keeps compiler-memoized reads fresh.
 * It holds only notifications newer than the latest `state` listing. */
export class FeedSnapshot {
  constructor(readonly version: number, private readonly runs: ReadonlyMap<string, RunProgress>, readonly workers: WorkerPool | null) {}
  progress(run: string): RunProgress | undefined {return this.runs.get(run);}
  /** The newest replication counts of a listed run: progress newer than the listing, else the listing's. */
  counts(run: {run_id: string; counts: Record<string, number>}): Record<string, number> {return this.runs.get(run.run_id)?.counts ?? run.counts;}
}

export class ExperimentFeed {
  #progress = new Map<string, {seq: number; value: RunProgress}>();
  #workers: {seq: number; value: WorkerPool} | null = null;
  #listeners = new Set<() => void>();
  #timer: ReturnType<typeof setTimeout> | null = null;
  #snapshot = new FeedSnapshot(0, new Map(), null);
  #closed = false;
  // Local arrival order: a socket delivers in order, but event_seq restarts on reconnect and a
  // referenced body can resolve after a newer inline one.
  #arrival = 0;
  #listed = 0;  // Arrival of the latest state listing; anything that arrived before it is stale.
  /** `resolve` expands a body sent by HTTP reference (large runs); normally `client.reference`. */
  constructor(private readonly resolve: ((reference: Reference) => Promise<unknown>) | null = null, private readonly interval = 250) {}
  snapshot = (): FeedSnapshot => this.#snapshot;
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {this.#listeners.delete(listener);};
  };
  notification(message: Notification): void {
    if (this.#closed || (message.type !== 'experiment_progress' && message.type !== 'workers_state')) return;
    const body = message.body, order = message.arrival ?? this.#arrival + 1;
    this.#arrival = Math.max(this.#arrival, order);
    if (object(body) && Object.keys(body).length === 1 && object(body.ref)) {
      if (!this.resolve) return;
      void this.resolve(body.ref as unknown as Reference).then(value => this.apply(message.type, order, value)).catch(() => {});
    } else this.apply(message.type, order, body);
  }
  private apply(type: Notification['type'], seq: number, body: unknown): void {
    if (this.#closed || !object(body) || seq < this.#listed) return;
    if (type === 'workers_state') {
      if (this.#workers && this.#workers.seq > seq) return;
      this.#workers = {seq, value: body as unknown as WorkerPool};
    } else {
      const run = body.run_id;
      if (typeof run !== 'string' || (this.#progress.get(run)?.seq ?? 0) > seq) return;
      this.#progress.set(run, {seq, value: body as unknown as RunProgress});
    }
    if (this.#timer === null) this.#timer = setTimeout(() => this.flush(), this.interval);
  }
  /**
   * An authoritative `state` arrived: it supersedes every earlier progress and worker notification.
   * Run ids are content-derived, so a deleted, re-created or restarted run must not keep old counts.
   */
  observe(arrival = this.#arrival + 1): void {
    if (this.#closed) return;
    this.#arrival = Math.max(this.#arrival, arrival);
    this.#listed = Math.max(this.#listed, arrival);
    for(const [run, item] of this.#progress) if(item.seq <= this.#listed) this.#progress.delete(run);
    if(this.#workers && this.#workers.seq <= this.#listed) this.#workers = null;
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.flush();
  }
  private flush(): void {
    this.#timer = null;
    this.#snapshot = new FeedSnapshot(this.#snapshot.version + 1,
      new Map([...this.#progress].map(([run, item]) => [run, item.value])), this.#workers?.value ?? null);
    for (const listener of [...this.#listeners]) listener();
  }
  dispose(): void {
    this.#closed = true;
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.#timer = null; this.#listeners.clear();
  }
}
