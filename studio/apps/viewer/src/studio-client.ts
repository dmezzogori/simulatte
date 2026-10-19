import {validateProjectState, type ProjectState} from './workspace/state';
/** Data-only authenticated transport. Reconnect reconciles; mutations are never replayed. */
export interface Revision {number: number; digest: string;}
export interface AttemptKey {request_id: string; attempt_id: string; generation: number;}
export interface Reference {id: string; url: string; length: number; sha256: string;}
export interface Prefix {last_cursor?: readonly [number, number] | null; attempt: string; generation: number; length: number; sha256: string; url: string;}
export interface View {revision: Revision; key: AttemptKey; committed: boolean; layout: Reference;}
export interface StudioState {
  project?: ProjectState;
  revision: Revision | null;
  inputs: {options: Record<string, unknown>; issues: Record<string, unknown>} | null;
  startup: {phase: string; error: unknown};
  active: {key: AttemptKey; revision: number; status: string; details: unknown; prefix: Prefix | null} | null;
  results: {key: AttemptKey; revision: number; origin?: Revision; created: number; trace: Prefix | null}[];
  progress?: {attempt_id: string; body: Record<string, unknown>} | null;
  latest_attempt?: {key: AttemptKey; revision: number; status: string; details: unknown} | null;
  metadata: Reference | null;
  view: View | null;
  last_good: View | null;
  selected_view?: View | null;
  fatal: unknown;
  experiments?: ExperimentRun[];
  /** Project-relative ``*.experiment.json`` files of the non-source membership, sorted. */
  experiment_files?: string[];
  /** Project-relative path of the configured model file; with ``metadata.selected`` it forms the model reference. */
  model?: string;
  workers?: WorkerPool;
  replays?: ReplayEntry[];
}
/** Newest experiment runs (at most 20) with replication counts per state. */
export interface ExperimentRun {run_id: string; experiment_id: string; name: string; status: string; created: number; counts: Record<string, number>;}
export interface WorkerSlot {name: string; kind: string; worker: string | null; state: string; attempt: string | null; last_status: string | null; last_code: string | null;}
export interface WorkerPool {local: number; workers: {name: string; host: string; slots: number}[]; slots: WorkerSlot[]; error: string | null;}
export interface ReplayEntry {request_id: string; original: string; status: string; verified: boolean | null; reason: string | null; key: AttemptKey | null; trace: Prefix | null;}
export interface Estimate {n: number; mean: number | null; sd: number | null; half_width: number | null; lower: number | null; upper: number | null; state: string; level: number;}
export interface ConfigurationSummary {index: number; label: string; parameters: Record<string, unknown>; counts: Record<string, number>; mixed_runtime: boolean;}
export interface ReplicationEntry {configuration: number; index: number; status: string; attempt: string | null; trace: Prefix | null; request_id?: string | null;}
/** ``comparison.get`` report: ``ExperimentReport.to_dict()`` plus the committed trace of each listed replication. */
export interface ExperimentReport {
  run_id: string; name: string; status: string; level: number; effective_level: number; pointwise: boolean; m: number;
  configurations: ConfigurationSummary[];
  rows: {configuration: number; label: string; kpi: string; n_succeeded: number; estimate: Estimate}[];
  missing: {configuration: number; index: number; status: string; attempt: string | null}[]; missing_total: number;
  replications: ReplicationEntry[]; replications_total: number;
  baseline?: string; paired_m?: number; paired_effective_level?: number;
  paired?: {configuration: number; label: string; kpi: string; n: number; estimate: Estimate; missing: number[]}[];
}
/** One recording of a ``series.get`` response; points are ``[t, seq, value]``, bounded per key. */
export interface SeriesEntry {attempt: string; series: Record<string, {points: [number, number, number | null][]; truncated: boolean}>;}
/** Experiment commands fence on run ids and store state; their revision may be omitted. */
export type ExperimentCommand = 'experiment.define' | 'experiment.start' | 'experiment.cancel' | 'experiment.resume' | 'experiment.delete' | 'comparison.get' | 'series.get' | 'replay.start' | 'workers.get';
export type ProjectCommand = 'project.get' | 'project.configure' | 'model.prepare' | 'environment.prepare' | 'environment.select' | 'history.get' | 'history.annotate' | 'run.get' | 'run.stop_and_run' | 'model.auto';
export type Command = ProjectCommand | 'project.rescan' | 'state.get' | 'params.set' | 'run.start' | 'run.cancel' | 'layout.get' | 'layout.validate' | 'layout.write' | 'view.select' | ExperimentCommand;
export type NotificationType = 'state' | 'model_info' | 'revision_changed' | 'run_state' | 'progress' | 'trace_available' | 'kpi' | 'layout_resolved' | 'validation' | 'inputs_changed' | 'invalidated' | 'error'
  | 'experiment_state' | 'experiment_progress' | 'replay_state' | 'workers_state' | 'experiments_changed';
export type ConnectionStatus = 'connecting' | 'synchronizing' | 'connected' | 'reconnecting' | 'authentication_required' | 'disposed';
export interface Notification {v: 1; type: NotificationType; event_seq: number; body: unknown; revision?: Revision | null; attempt_id?: string; generation?: number;}
interface Options {
  origin?: string;
  socket?: (url: string) => WebSocket;
  fetch?: typeof fetch;
  onState?: (state: StudioState) => void;
  onStatus?: (status: ConnectionStatus) => void;
  onNotification?: (message: Notification) => void;
  onError?: (error: unknown) => void;
}
interface Pending {resolve(value: Record<string, unknown>): void; reject(error: Error): void; timer: ReturnType<typeof setTimeout>;}
const MAX_MESSAGE = 256 << 10;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
function serialize(value: unknown, limit = MAX_MESSAGE): string {
  const ancestors = new Set<object>();
  const encode = (item: unknown, depth: number): string => {
    if (depth > 64) throw new Error('Control nesting limit exceeded');
    if (item === null || typeof item === 'string' || typeof item === 'boolean') return JSON.stringify(item);
    if (typeof item === 'number') {
      if (!Number.isFinite(item)) throw new Error('Control numbers must be finite');
      // JS has one numeric type. Preserve a large finite float with an explicit
      // exponent so Python cannot misread JSON.stringify(1e20) as an unsafe int.
      return Number.isInteger(item) && !Number.isSafeInteger(item) ? item.toExponential() : JSON.stringify(item);
    }
    if (typeof item !== 'object' || item === null ||
        !Array.isArray(item) && Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null)
      throw new Error('Unsupported control value');
    if (ancestors.has(item)) throw new Error('Cyclic control value');
    ancestors.add(item);
    try {
      if (Array.isArray(item)) return '[' + Array.from(item, child => encode(child ?? null, depth + 1)).join(',') + ']';
      return '{' + Object.entries(item).filter(([, child]) => child !== undefined)
        .map(([key, child]) => JSON.stringify(key) + ':' + encode(child, depth + 1)).join(',') + '}';
    } finally {ancestors.delete(item);}
  };
  const raw = encode(value, 0);
  if (new TextEncoder().encode(raw).length > limit) throw new Error('Message byte limit exceeded');
  return raw;
}
export class StudioClient {
  state: StudioState | null = null;
  status: ConnectionStatus = 'connecting';
  readonly origin: string;
  #socket: WebSocket | null = null;
  #epoch = 0;
  #sequence = 0;
  #pending = new Map<string, Pending>();
  #retry: ReturnType<typeof setTimeout> | null = null;
  #delay = 1000;
  #abort = new AbortController();
  #statePending: unknown = undefined;
  #stateReading = false;
  #notifications = new Set<(message: Notification) => void>();
  #states = new Set<(state: StudioState) => void>();
  constructor(private readonly secret: string, private readonly options: Options = {}) {
    this.origin = options.origin ?? location.origin;
  }
  subscribeNotifications(listener: (message: Notification) => void): () => void {
    this.#notifications.add(listener); return () => {this.#notifications.delete(listener);};
  }
  subscribeState(listener: (state: StudioState) => void): () => void {
    this.#states.add(listener); return () => {this.#states.delete(listener);};
  }
  private statusChanged(status: ConnectionStatus): void {this.status = status; this.options.onStatus?.(status);}
  private current(socket: WebSocket, epoch: number): boolean {return this.#socket === socket && this.#epoch === epoch;}
  connect(): void {
    if (this.status === 'disposed' || this.#socket) return;
    if (this.#retry) clearTimeout(this.#retry); this.#retry = null;
    this.statusChanged(this.state ? 'reconnecting' : 'connecting');
    const url = new URL('/ws', this.origin); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = (this.options.socket ?? (url => new WebSocket(url)))(url.href);
    this.#socket = socket; const epoch = ++this.#epoch;
    this.#sequence = 0; this.#abort = new AbortController();
    socket.onopen = () => {
      if (!this.current(socket, epoch)) return;
      this.statusChanged('synchronizing'); socket.send(serialize({v: 1, type: 'auth', secret: this.secret}));
    };
    socket.onmessage = event => {
      if (!this.current(socket, epoch)) return;
      try {
        if (typeof event.data !== 'string' || new TextEncoder().encode(event.data).length > MAX_MESSAGE)
          throw new Error('Invalid Studio control frame');
        const message: unknown = JSON.parse(event.data);
        if (!object(message) || message.v !== 1 || typeof message.type !== 'string') throw new Error('Unsupported Studio protocol');
        if (message.type === 'response') {
          if (typeof message.id !== 'string') throw new Error('Invalid command response');
          const pending = this.#pending.get(message.id);
          if (pending) {
            const id = message.id;
            void this.expand(message.body, this.#abort.signal).then(body => {
              if (!this.current(socket, epoch) || !this.#pending.delete(id)) return;
              clearTimeout(pending.timer);
              if (!object(body)) pending.reject(new Error('Invalid command outcome'));
              else pending.resolve(body);
            }).catch(error => {
              if (this.#pending.delete(id)) {clearTimeout(pending.timer); pending.reject(error);}
            });
          }
          return;
        }
        if (!Number.isSafeInteger(message.event_seq) || (message.event_seq as number) < 1) throw new Error('Invalid notification sequence');
        if ((message.event_seq as number) <= this.#sequence) return;
        this.#sequence = message.event_seq as number;
        if (message.type === 'state') {
          this.#statePending = message.body;
          if (!this.#stateReading) void this.readState(socket, epoch);
        } else {
          this.options.onNotification?.(message as unknown as Notification);
          for(const listener of [...this.#notifications]) listener(message as unknown as Notification);
        }
      } catch (error) {this.options.onError?.(error); socket.close(1002);}
    };
    socket.onclose = event => {
      if (!this.current(socket, epoch)) return;
      this.#socket = null; this.#epoch++; this.#abort.abort();
      this.#statePending = undefined; this.#stateReading = false;
      for (const pending of this.#pending.values()) {
        clearTimeout(pending.timer); pending.reject(new Error('Command outcome is unknown; resynchronize before trying again'));
      }
      this.#pending.clear();
      if (event.code === 1008) {this.statusChanged('authentication_required'); return;}
      this.statusChanged('reconnecting');
      if (event.code === 1006) void this.checkAuthentication(this.#epoch);
      else this.retry();
    };
    socket.onerror = () => { /* Close drives recovery without exposing credential-bearing messages. */ };
  }
  private retry(): void {
    this.#retry = setTimeout(() => {this.#retry = null; this.connect();}, this.#delay);
    this.#delay = Math.min(30000, this.#delay * 2);
  }
  private async checkAuthentication(epoch: number): Promise<void> {
    // Browsers hide HTTP upgrade-denial status behind close code 1006. A bounded
    // authenticated HEAD distinguishes an expired instance from a network loss.
    const abort = new AbortController(); this.#abort = abort;
    const timeout = setTimeout(() => abort.abort(), 5000);
    try {
      const response = await (this.options.fetch ?? fetch)(new URL('/api/state', this.origin), {
        method: 'HEAD', headers: {'X-Simulatte-Session': this.secret},
        credentials: 'same-origin', redirect: 'error', signal: abort.signal,
      });
      if (epoch !== this.#epoch) return;
      if (response.status === 403) {this.statusChanged('authentication_required'); return;}
    } catch { /* A network outage can recover without a new capability. */ }
    finally {clearTimeout(timeout);}
    if (epoch === this.#epoch) this.retry();
  }
  private async readState(socket: WebSocket, epoch: number): Promise<void> {
    this.#stateReading = true;
    try {
      while (this.#statePending !== undefined && this.current(socket, epoch)) {
        const next = this.#statePending; this.#statePending = undefined;
        const state = await this.expand(next, this.#abort.signal);
        if (!this.current(socket, epoch)) return;
        if (this.#statePending !== undefined) continue;
        if (!object(state) || !('revision' in state) || !Array.isArray(state.results) || !object(state.startup)) throw new Error('Invalid Studio state');
        if (state.project !== undefined) validateProjectState(state.project);
        this.state = state as unknown as StudioState;
        this.#delay = 1000; this.statusChanged('connected'); this.options.onState?.(this.state);
        for(const listener of [...this.#states]) listener(this.state);
      }
    } catch (error) {
      if (this.current(socket, epoch)) {this.options.onError?.(error); socket.close(1012);}
    } finally {if (this.current(socket, epoch)) this.#stateReading = false;}
  }
  private async expand(value: unknown, signal: AbortSignal): Promise<unknown> {
    if (object(value) && Object.keys(value).length === 1 && object(value.ref)) return this.reference(value.ref as unknown as Reference, signal);
    return value;
  }
  async reference(reference: Reference, signal?: AbortSignal): Promise<unknown> {
    if (!/^[a-f0-9]{64}$/.test(reference.id) || reference.sha256 !== reference.id ||
        reference.url !== `/api/references/${reference.id}` || !Number.isSafeInteger(reference.length) ||
        reference.length < 0 || reference.length > 128 << 20) throw new Error('Invalid HTTP reference');
    const response = await (this.options.fetch ?? fetch)(new URL(reference.url, this.origin), {
      headers: {'X-Simulatte-Session': this.secret}, credentials: 'same-origin', redirect: 'error', signal: signal ?? null,
    });
    if (!response.ok || !response.body) throw new Error('Reference unavailable; resynchronize Studio');
    const reader = response.body.getReader(); const bytes = new Uint8Array(reference.length); let offset = 0;
    try {
      while (true) {
        const {value, done} = await reader.read(); if (done) break;
        if (offset + value.length > bytes.length) throw new Error('Reference exceeds declared length');
        bytes.set(value, offset); offset += value.length;
      }
    } catch (error) {await reader.cancel(); throw error;}
    finally {reader.releaseLock();}
    if (offset !== bytes.length) throw new Error('Reference length mismatch');
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
    if (hash !== reference.sha256) throw new Error('Reference hash mismatch');
    return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
  }
  async command(type: Command, body: Record<string, unknown> = {}, options: {revision?: Revision | null; key?: AttemptKey} = {}): Promise<Record<string, unknown>> {
    const socket = this.#socket;
    if (this.status !== 'connected' || !socket || socket.readyState !== 1) throw new Error('Studio must synchronize before commands');
    if (this.#pending.size >= 32 || socket.bufferedAmount > 1 << 20) throw new Error('Studio command queue is full');
    const id = crypto.randomUUID();
    const raw = serialize({v: 1, type, id, revision: options.revision === undefined ? this.state?.revision ?? null : options.revision, body,
      ...(options.key ? {attempt_id: options.key.attempt_id, generation: options.key.generation} : {})});
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id); reject(new Error('Command outcome is unknown; resynchronize before trying again'));
      }, type === 'environment.prepare' ? 660000 : 30000);
      this.#pending.set(id, {resolve, reject, timer});
      try {socket.send(raw);} catch (error) {clearTimeout(timer); this.#pending.delete(id); reject(error);}
    });
  }
  async cancelPreparation(operationId: string): Promise<void> {
    const response = await (this.options.fetch ?? fetch)(new URL('/api/environment/cancel/' + encodeURIComponent(operationId), this.origin), {
      method: 'PUT', headers: {'X-Simulatte-Session': this.secret}, credentials: 'same-origin', redirect: 'error', signal: this.#abort.signal,
    });
    if (!response.ok) throw new Error('Environment cancellation failed; check the preparation log');
  }
  async resync(): Promise<void> {
    const sequence = this.#sequence;
    const result = await this.command('state.get');
    if (!result.ok) throw new Error('Studio resynchronization failed');
    // The server also broadcasts authoritative changes. Never replay a mutation here.
    const socket = this.#socket;
    if (socket && sequence === this.#sequence) {this.#statePending = result.state; if (!this.#stateReading) await this.readState(socket, this.#epoch);}
  }
  async writeLayout(layer: Record<string, unknown>, revision: Revision, signal?: AbortSignal, modelId?: string): Promise<Record<string, unknown>> {
    return this.layoutCommand(false, layer, revision, signal, modelId);
  }
  async validateLayout(layer: Record<string, unknown>, revision: Revision, signal?: AbortSignal, modelId?: string): Promise<Record<string, unknown>> {
    return this.layoutCommand(true, layer, revision, signal, modelId);
  }
  private async layoutCommand(validate: boolean, layer: Record<string, unknown>, revision: Revision, signal?: AbortSignal, modelId?: string): Promise<Record<string, unknown>> {
    if (this.status !== 'connected') throw new Error('Studio must synchronize before editing');
    const raw = serialize(layer, 16 << 20);
    const response = await (this.options.fetch ?? fetch)(new URL(validate ? '/api/layout/validate' : '/api/layout', this.origin), {
      method: 'PUT', headers: {'X-Simulatte-Session': this.secret, 'Content-Type': 'application/json',
        'X-Simulatte-Revision': JSON.stringify(revision), 'X-Simulatte-Command': crypto.randomUUID(), ...(modelId ? {'X-Simulatte-Model':modelId} : {})},
      body: raw, credentials: 'same-origin', redirect: 'error', signal: signal ?? null,
    });
    const result: unknown = await this.expand(await response.json(), signal ?? this.#abort.signal);
    if (!response.ok || !object(result)) throw new Error(response.status === 409
      ? 'Layout changed; keep your draft and reload its base before saving'
      : 'Layout save failed; resynchronize before trying again');
    return result;
  }
  dispose(): void {
    if (this.#retry) clearTimeout(this.#retry); this.#retry = null;
    const socket = this.#socket; this.#socket = null; this.#epoch++; this.#abort.abort();
    for (const pending of this.#pending.values()) {clearTimeout(pending.timer); pending.reject(new Error('Studio client disposed'));}
    this.#pending.clear(); this.#notifications.clear(); this.#states.clear(); socket?.close(1000); this.statusChanged('disposed');
  }
}
