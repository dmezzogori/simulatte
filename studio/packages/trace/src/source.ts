import type { ByteSource } from "./container";

/** Immutable HTTP snapshot. Never silently accept a server ignoring Range or changing the file. */
export class HttpSource implements ByteSource {
  private constructor(
    readonly url: string,
    readonly size: number,
    readonly etag: string,
    private readonly headers: Readonly<Record<string, string>>,
  ) {}
  static async open(
    url: string,
    headers: Readonly<Record<string, string>> = {},
    signal?: AbortSignal,
  ): Promise<HttpSource> {
    const response = await fetch(url, {
      method: "HEAD",
      headers,
      signal: signal ?? null,
      cache: "no-store",
    });
    if (!response.ok)
      throw new Error(`Trace request failed (${response.status})`);
    const length = response.headers.get("content-length");
    const size = length !== null && /^\d+$/.test(length) ? Number(length) : NaN;
    const etag = response.headers.get("etag");
    if (
      !Number.isSafeInteger(size) ||
      size < 0 ||
      !etag ||
      !/^"[^"\r\n]*"$/.test(etag)
    )
      throw new Error("Trace server must provide size and ETag");
    return new HttpSource(url, size, etag, Object.freeze({ ...headers }));
  }
  async read(
    offset: number,
    length: number,
    signal?: AbortSignal,
  ): Promise<Uint8Array> {
    if (
      !Number.isSafeInteger(offset) ||
      offset < 0 ||
      !Number.isSafeInteger(length) ||
      length < 0
    ) {
      throw new RangeError("Invalid byte range");
    }
    const end = Math.min(this.size, offset + length);
    if (end <= offset) return new Uint8Array();
    const response = await fetch(this.url, {
      headers: {
        ...this.headers,
        Range: `bytes=${offset}-${end - 1}`,
        "If-Match": this.etag,
      },
      signal: signal ?? null,
      cache: "no-store",
    });
    if (
      response.status !== 206 ||
      response.headers.get("etag") !== this.etag ||
      response.headers.get("content-range") !==
        `bytes ${offset}-${end - 1}/${this.size}`
    ) {
      throw new Error(
        "Trace changed or range response was invalid; reopen the file",
      );
    }
    if (!response.body) throw new Error("Missing trace response body");
    const result = new Uint8Array(end - offset),
      reader = response.body.getReader();
    let written = 0;
    try {
      for (;;) {
        signal?.throwIfAborted();
        const { done, value } = await reader.read();
        if (done) break;
        if (written + value.length > result.length)
          throw new Error("Oversized trace range response");
        result.set(value, written);
        written += value.length;
      }
    } catch (error) {
      await reader.cancel();
      throw error;
    } finally {
      reader.releaseLock();
    }
    if (written !== result.length)
      throw new Error("Short trace range response");
    return result;
  }
}
