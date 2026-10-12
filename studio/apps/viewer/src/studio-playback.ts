/** Reconciles authoritative execution state with the imperative SP3 viewer. */
import {HttpSource, type LayoutBundle} from '@simulatte/trace';
import {LiveSession} from './live-session';
import type {Controller} from './controller';
import type {StudioClient, StudioState, Notification, Prefix, View} from './studio-client';

export class StudioPlayback {
  #epoch = 0;
  #abort: AbortController | null = null;
  #live: LiveSession | null = null;
  #attempt: string | null = null;
  #layout: string | null = null;
  #opening: string | null = null;
  #detached = false;
  #closed = false;
  constructor(private readonly controller: Controller, private readonly client: StudioClient,
    private readonly headers: Readonly<Record<string, string>>, private readonly onError: (error: string) => void) {}
  get live(): LiveSession | null {return this.#live;}
  /** Opening a local file or explicitly selecting history suspends automatic switching. */
  detach(): void {this.#detached = true; this.cancelPending(); this.#live = null; this.#attempt = null; this.#layout = null;}
  select(prefix: Prefix, revision: number, view: View | null): void {
    this.#detached = true;
    // Controller.open exposes its source before playback preparation finishes.
    // Force another open to fence that operation through the Controller as well.
    if (this.#opening !== null) this.#attempt = null;
    this.cancelPending();
    this.openCommitted(prefix, revision, view);
  }
  resume(): void {this.#detached = false; if (this.client.state) this.update(this.client.state);}
  private cancelPending(): number {this.#abort?.abort(); this.#abort = null; this.#opening = null; return ++this.#epoch;}
  update(state: StudioState): void {
    if (this.#closed || this.#detached) return;
    const active = state.active;
    if (active && ['ready', 'running', 'finalizing'].includes(active.status) && active.prefix) {
      if (this.#attempt !== active.key.attempt_id || !this.#live) {
        this.cancelPending();
        this.#attempt = active.key.attempt_id; this.#layout = null;
        const live = new LiveSession(async (descriptor, signal) => HttpSource.open(
          `/api/prefix/${descriptor.attempt}/${descriptor.generation}`, this.headers, signal));
        this.#live = live; live.reset(active.key.attempt_id, String(active.revision));
        // Follow is explicit user intent; a new attempt opens at activation.
        void this.controller.attachLive(live, `Run · revision ${active.revision}`).catch(error => this.onError(String(error)));
      }
      this.#live!.update(active.prefix);
      this.applyView(state.view, this.#epoch);
      return;
    }
    if (this.#live && active?.key.attempt_id !== this.#attempt) {
      const result = state.results.find(item => item.key.attempt_id === this.#attempt);
      if (result?.trace) {
        this.#live.terminal('completed');
        this.openCommitted(result.trace, result.revision, state.view?.key.attempt_id === this.#attempt ? state.view : state.last_good);
        return;
      }
      this.#live.invalidate(); this.#live = null; this.#attempt = null; this.#layout = null;
    }
    const view = state.view?.committed ? state.view : state.last_good;
    const result = state.results.find(item => item.key.attempt_id === view?.key.attempt_id) ?? state.results[0];
    if (result?.trace) this.openCommitted(result.trace, result.revision, view?.key.attempt_id === result.key.attempt_id ? view : null);
  }
  notification(message: Notification): void {
    if (this.#closed || this.#detached || !this.#live || message.attempt_id !== this.#attempt) return;
    if (message.type === 'trace_available') {
      const prefix = message.body as Prefix;
      if (prefix.attempt === this.#attempt) this.#live.update(prefix);
    } else if (message.type === 'run_state') {
      const status = (message.body as {status?: string}).status;
      if (status && ['failed', 'lost', 'disk_full', 'validation_failed', 'cancelled'].includes(status)) {
        this.#live.terminal(status === 'cancelled' ? 'cancelled' : 'failed');
      }
    }
  }
  private applyView(view: View | null, epoch: number): void {
    if (!view || view.key.attempt_id !== this.#attempt || view.layout.sha256 === this.#layout) return;
    const hash = view.layout.sha256;
    this.#layout = hash;
    void this.client.reference(view.layout).then(async layout => {
      if (epoch !== this.#epoch || hash !== this.#layout) return;
      await this.controller.setPresentation(layout as LayoutBundle);
    }).catch(error => {if (epoch === this.#epoch && hash === this.#layout) {this.#layout = null; this.onError(String(error));}});
  }
  private openCommitted(prefix: Prefix, revision: number, view: View | null): void {
    if (this.#opening === prefix.attempt) return;
    if (this.#attempt === prefix.attempt && !this.#live && this.controller.source) {
      if (view) this.applyView(view, this.#epoch);
      else if (this.#layout !== null) {
        const epoch = this.cancelPending();
        this.#layout = null;
        void this.controller.setPresentation(null).catch(error => {
          if (epoch === this.#epoch) this.onError(String(error));
        });
      }
      return;
    }
    const position = this.#attempt === prefix.attempt ? this.#live?.frame ?? undefined : undefined;
    const rate = this.controller.playback?.rate ?? 1;
    const epoch = this.cancelPending();
    const abort = new AbortController(); this.#abort = abort; this.#opening = prefix.attempt;
    void Promise.all([
      HttpSource.open(prefix.url, this.headers, abort.signal),
      view ? this.client.reference(view.layout, abort.signal) : Promise.resolve(null),
    ]).then(async ([source, layout]) => {
      if (epoch !== this.#epoch) return;
      this.#attempt = prefix.attempt; this.#live = null; this.#layout = view?.layout.sha256 ?? null;
      await this.controller.open(source, `Run · revision ${revision}`, layout as LayoutBundle | null, position);
      if (epoch === this.#epoch && this.controller.playback) this.controller.playback.rate = rate;
    }).catch(error => {if (epoch === this.#epoch) this.onError(String(error));})
      .finally(() => {if (epoch === this.#epoch) this.#opening = null;});
  }
  dispose(): void {this.#closed = true; this.cancelPending(); this.#live?.dispose(); this.#live = null;}
}
