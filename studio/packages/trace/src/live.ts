/** Incremental committed prefixes. Attempt identity and SHA-256 fence every generation. */
import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';
import { TraceCorrupted, resolveLimits, type ByteSource } from './container';
import { Recording, type RecordingView } from './recording';
import { Trace, type OpenOptions } from './trace';

export interface PrefixDescriptor {
  readonly attempt: string;
  readonly generation: number;
  readonly length: number;
  readonly sha256: string;
}
export type PrefixProvider = (descriptor: PrefixDescriptor, signal?: AbortSignal) => ByteSource | Promise<ByteSource>;
function checked(raw: PrefixDescriptor): PrefixDescriptor {
  if (!/^[a-f0-9]{32}$/.test(raw.attempt) || !/^[a-f0-9]{64}$/.test(raw.sha256) ||
      !Number.isSafeInteger(raw.generation) || raw.generation < 1 ||
      !Number.isSafeInteger(raw.length) || raw.length < 12)
    throw new TraceCorrupted('Invalid live prefix descriptor');
  return Object.freeze({...raw});
}
class ReadSlots {
  #active = 0;
  #waiters = new Set<() => void>();
  async read(source: ByteSource, offset: number, length: number, signal?: AbortSignal): Promise<Uint8Array> {
    while (this.#active >= 2) {
      await new Promise<void>((resolve, reject) => {
        const wake = () => { cleanup(); resolve(); };
        const abort = () => { cleanup(); reject(signal?.reason); };
        const cleanup = () => { this.#waiters.delete(wake); signal?.removeEventListener('abort', abort); };
        this.#waiters.add(wake);
        signal?.addEventListener('abort', abort, {once: true});
        if (signal?.aborted) abort();
      });
    }
    signal?.throwIfAborted();
    this.#active++;
    try { return await source.read(offset, length, signal); }
    finally { this.#active--; this.#waiters.values().next().value?.(); }
  }
}
/** Each extent keeps its original immutable source, even after a later generation arrives. */
export class PrefixSource implements ByteSource {
  readonly descriptor: PrefixDescriptor;
  readonly size: number;
  private readonly slots: ReadSlots;
  constructor(descriptor: PrefixDescriptor, private readonly source: ByteSource,
    private readonly previous?: PrefixSource) {
    this.slots = previous?.slots ?? new ReadSlots();
    this.descriptor = checked(descriptor);
    this.size = descriptor.length;
    if (source.size !== this.size) throw new TraceCorrupted('Prefix source length mismatch');
    if (previous && (previous.descriptor.attempt !== descriptor.attempt ||
        previous.size >= this.size || previous.descriptor.generation >= descriptor.generation))
      throw new TraceCorrupted('Invalid prefix extension');
  }
  async read(offset: number, length: number, signal?: AbortSignal): Promise<Uint8Array> {
    signal?.throwIfAborted();
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(length) || length < 0 ||
        !Number.isSafeInteger(offset + length)) throw new RangeError('Invalid prefix range');
    const end = Math.min(this.size, offset + length);
    if (end <= offset) return new Uint8Array();
    // Walk iteratively: thousands of generations must not exhaust the call stack.
    const parts: {source: ByteSource; start: number; end: number}[] = [];
    let current: PrefixSource | undefined = this;
    while (current && offset < end) {
      const start = Math.max(offset, current.previous?.size ?? 0);
      const stop = Math.min(end, current.size);
      if (stop > start) parts.push({source: current.source, start, end: stop});
      if (!current.previous || offset >= current.previous.size) break;
      current = current.previous;
    }
    const result = new Uint8Array(end - offset);
    for (const part of parts.reverse()) {
      const data = await this.slots.read(part.source, part.start, part.end - part.start, signal);
      signal?.throwIfAborted();
      if (data.length !== part.end - part.start) throw new TraceCorrupted('Short immutable prefix read');
      result.set(data, part.start - offset);
    }
    return result;
  }
}
export class LiveRecording {
  #prefix!: PrefixSource;
  #recording!: Recording;
  #hash = sha256.create();
  #extending = false;
  #disposed = false;
  #active: AbortController | null = null;
  private constructor(private readonly provider: PrefixProvider, private readonly options: OpenOptions) {}
  static async open(descriptor: PrefixDescriptor, provider: PrefixProvider,
    options: OpenOptions = {}, signal?: AbortSignal): Promise<LiveRecording> {
    const live = new LiveRecording(provider, options);
    await live.extend(descriptor, signal);
    return live;
  }
  get generation(): number { return this.#prefix.descriptor.generation; }
  get descriptor(): PrefixDescriptor { return this.#prefix.descriptor; }
  snapshot(): RecordingView { return this.#recording; }
  async extend(descriptor: PrefixDescriptor, signal?: AbortSignal): Promise<RecordingView> {
    if (this.#disposed) throw new Error('Live recording is disposed');
    if (this.#extending) throw new Error('Live extension already in progress');
    descriptor = checked(descriptor);
    if (this.#prefix) {
      if (descriptor.attempt !== this.descriptor.attempt) throw new TraceCorrupted('Live attempt changed');
      if (descriptor.generation <= this.generation) throw new TraceCorrupted('Live generation did not advance');
      if (descriptor.length <= this.#prefix.size) throw new TraceCorrupted('Live prefix did not grow');
    }
    this.#extending = true;
    const controller = new AbortController();
    this.#active = controller;
    const abort = () => controller.abort(signal?.reason);
    signal?.addEventListener('abort', abort, {once: true});
    if (signal?.aborted) abort();
    try {
      const active = controller.signal;
      active.throwIfAborted();
      const source = new PrefixSource(descriptor, await this.provider(descriptor, active), this.#prefix);
      const hash = this.#hash.clone();
      const start = this.#prefix?.size ?? 0;
      // Bounded streaming digest, no full-prefix allocation or rehash of old bytes.
      for (let offset = start; offset < source.size; offset += 1 << 20) {
        hash.update(await source.read(offset, Math.min(1 << 20, source.size - offset), active));
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      if (bytesToHex(hash.clone().digest()) !== descriptor.sha256)
        throw new TraceCorrupted('Live prefix hash mismatch');
      const trace = await Trace.committed(source, this.options, this.#recording?.trace, active);
      const recording = new Recording(trace, source, resolveLimits(this.options.limits));
      await recording.load(active, start || 12, this.#recording);
      active.throwIfAborted();
      this.#hash = hash;
      this.#prefix = source;
      this.#recording = recording;
      return recording;
    } finally {
      signal?.removeEventListener('abort', abort);
      this.#active = null;
      this.#extending = false;
    }
  }
  dispose(): void {
    this.#disposed = true;
    this.#active?.abort(new DOMException('Live recording disposed', 'AbortError'));
  }
}
