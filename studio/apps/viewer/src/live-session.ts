/** Live transport adapter. Network generations and playback frames have separate fences. */
import {LiveRecording, type PrefixDescriptor, type PrefixProvider, type RecordingView} from '@simulatte/trace';
import {Playback, Session, type Clock, type Position, type PreparedFrame} from '@simulatte/playback';

export interface LiveSessionState {
  readonly attempt: string | null;
  readonly revision: string | null;
  readonly generation: number | null;
  readonly recording: RecordingView | null;
  readonly frame: PreparedFrame | null;
  readonly following: boolean;
  readonly terminal: 'completed' | 'failed' | 'cancelled' | null;
  readonly status: 'loading' | 'paused' | 'playing' | 'buffering' | 'complete' | 'invalidated' | 'error' | 'disposed';
  readonly error: unknown;
}
interface Options {clock?: Clock; onChange?: (state: LiveSessionState) => void;}
export class LiveSession {
  #epoch = 0;
  #live: LiveRecording | null = null;
  #playback: Playback | null = null;
  #unsubscribe: (() => void) | null = null;
  #pending: PrefixDescriptor | null = null;
  #runner: Promise<void> | null = null;
  #abort: AbortController | null = null;
  #series: readonly string[] = [];
  #state: LiveSessionState = Object.freeze({attempt: null, revision: null, generation: null,
    recording: null, frame: null, following: false, terminal: null, status: 'loading', error: null});
  constructor(private readonly provider: PrefixProvider, private readonly options: Options = {}) {}
  get state(): LiveSessionState {return this.#state;}
  private publish(change: Partial<LiveSessionState>): void {
    this.#state = Object.freeze({...this.#state, ...change});
    this.options.onChange?.(this.#state);
  }
  private stop(): void {
    this.#epoch++;
    this.#abort?.abort(); this.#abort = null;
    this.#live?.dispose(); this.#live = null;
    this.#unsubscribe?.(); this.#unsubscribe = null;
    this.#playback?.dispose(); this.#playback = null;
    this.#pending = null; this.#runner = null;
  }
  reset(attempt: string, revision: string): void {
    if (this.state.status === 'disposed') throw new Error('Live session disposed');
    this.stop();
    this.publish({attempt, revision, generation: null, recording: null, frame: null,
      following: false, terminal: null, status: 'loading', error: null});
  }
  /** Only the latest pending descriptor is retained; no queue grows with websocket notifications. */
  update(prefix: PrefixDescriptor): void {
    if (this.state.status === 'disposed' || this.state.status === 'invalidated') return;
    if (prefix.attempt !== this.state.attempt) return;
    const latest = this.#pending?.generation ?? this.state.generation ?? 0;
    if (prefix.generation <= latest) return;
    this.#pending = Object.freeze({...prefix});
    if (!this.#runner) {
      const epoch = this.#epoch;
      this.#runner = this.drain(epoch).finally(() => {if (epoch === this.#epoch) this.#runner = null;});
    }
  }
  idle(): Promise<void> {return this.#runner ?? Promise.resolve();}
  private async drain(epoch: number): Promise<void> {
    while (this.#pending && epoch === this.#epoch) {
      const descriptor = this.#pending; this.#pending = null;
      const abort = new AbortController(); this.#abort = abort;
      try {
        if (this.#live && descriptor.generation <= this.#live.generation) continue;
        const live = this.#live;
        const opened = live ?? await LiveRecording.open(descriptor, this.provider, {}, abort.signal);
        if (live) await live.extend(descriptor, abort.signal);
        if (epoch !== this.#epoch) {opened.dispose(); return;}
        this.#live = opened;
        const recording = opened.snapshot();
        const oldPosition = this.#playback?.frame;
        const playing = this.#playback?.status === 'playing';
        const rate = this.#playback?.rate ?? 1;
        this.#unsubscribe?.(); this.#playback?.dispose();
        const session = new Session(recording); session.setSeries(this.#series);
        const playback = new Playback(session, this.options.clock); playback.rate = rate;
        this.#playback = playback;
        this.publish({recording, generation: opened.generation, error: null});
        this.#unsubscribe = playback.subscribe((frame, status, error) => {
          if (epoch !== this.#epoch || this.#playback !== playback) return;
          const display = status === 'ended' ? 'paused' : status;
          this.publish({frame, status: error ? 'error' : this.state.following && display === 'paused'
            ? this.state.terminal === 'completed' ? 'complete' : 'buffering' : display,
            error: error ?? null});
        });
        if (this.state.following) await this.edge();
        else {
          await playback.seek(oldPosition ?? {mode: 'activation', time: recording.trace.activation?.[0] ?? 0,
            cursor: recording.trace.activation});
          if (playing && epoch === this.#epoch && this.#playback === playback) playback.play();
        }
      } catch (error) {
        if (epoch === this.#epoch) this.publish({status: 'error', error});
      } finally {if (epoch === this.#epoch) this.#abort = null;}
    }
  }
  private usable(): void {
    if (this.state.status === "disposed" || this.state.status === "invalidated")
      throw new Error(`Live session ${this.state.status}`);
  }
  setSeries(keys: readonly string[]): void {
    this.usable();
    if (keys.length > 8) throw new RangeError('At most eight active series');
    this.#series = Object.freeze([...keys]); this.#playback?.session.setSeries(keys);
  }
  private async edge(): Promise<void> {
    const recording = this.state.recording;
    if (!recording || !this.#playback) return;
    const end = recording.finiteRange?.[1];
    await this.#playback.seek(end === undefined
      ? {mode: 'activation', time: recording.trace.activation?.[0] ?? 0, cursor: recording.trace.activation}
      : {mode: 'continuous', time: end, cursor: null});
  }
  async follow(): Promise<void> {this.usable(); this.publish({following: true}); await this.edge();}
  async seek(position: Position): Promise<void> {
    this.usable(); this.publish({following: false}); await this.#playback?.seek(position);
  }
  async step(direction: 1 | -1): Promise<void> {
    this.usable(); this.publish({following: false}); await this.#playback?.step(direction);
  }
  play(): void {this.usable(); this.publish({following: false}); this.#playback?.play();}
  pause(): void {this.usable(); this.publish({following: false}); this.#playback?.pause();}
  terminal(status: NonNullable<LiveSessionState['terminal']>): void {
    if (this.state.status === 'disposed' || this.state.status === 'invalidated') return;
    if (status !== 'completed') {this.invalidate(); this.publish({terminal: status}); return;}
    this.publish({terminal: status, ...(this.state.following ? {status: 'complete' as const} : {})});
  }
  invalidate(): void {
    if (this.state.status === 'disposed') return;
    this.stop(); this.publish({recording: null, frame: null, status: 'invalidated'});
  }
  dispose(): void {this.stop(); this.publish({recording: null, frame: null, status: 'disposed'});}
}
