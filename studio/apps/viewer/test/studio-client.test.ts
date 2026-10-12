import {describe, expect, it, vi} from 'vitest';
import {StudioClient} from '../src/studio-client';

class Socket {
  readyState = 0;
  bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: {data: string}) => void) | null = null;
  onclose: ((event: {code: number}) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: Record<string, unknown>[] = [];
  send(raw: string) {this.sent.push(JSON.parse(raw));}
  close(code = 1000) {this.readyState = 3; this.onclose?.({code});}
  open() {this.readyState = 1; this.onopen?.();}
  message(value: unknown) {this.onmessage?.({data: JSON.stringify(value)});}
}
const revision = {number: 1, digest: 'a'.repeat(64)};
const state = {revision, inputs: null, startup: {phase: 'ready', error: null}, active: null, results: [], metadata: null, view: null, last_good: null, fatal: null};
function setup() {
  const sockets: Socket[] = [];
  const client = new StudioClient('secret', {origin: 'http://127.0.0.1:8000', socket: () => {
    const socket = new Socket(); sockets.push(socket); return socket as unknown as WebSocket;
  }});
  client.connect(); const socket = sockets[0]!; socket.open();
  return {client, socket, sockets};
}
const tick = async () => {await Promise.resolve(); await Promise.resolve();};

describe('Studio transport', () => {
  it('authenticates in the first frame, waits for authoritative state, and never puts secrets in URLs', async () => {
    const {client, socket} = setup();
    expect(socket.sent).toEqual([{v: 1, type: 'auth', secret: 'secret'}]);
    await expect(client.command('run.start')).rejects.toThrow(/synchron/);
    socket.message({v: 1, type: 'state', event_seq: 1, body: state}); await tick();
    expect(client.state).toEqual(state); expect(client.status).toBe('connected');
    const command = client.command('run.start'); const sent = socket.sent.at(-1)!;
    expect(sent.revision).toEqual(revision); expect(sent).not.toHaveProperty('secret');
    socket.message({v: 1, type: 'response', id: sent.id, body: {ok: true}});
    await expect(command).resolves.toEqual({ok: true}); client.dispose();
  });
  it('rejects uncertain commands on disconnect and resynchronizes without replay', async () => {
    vi.useFakeTimers();
    const {client, socket, sockets} = setup();
    socket.message({v: 1, type: 'state', event_seq: 3, body: state}); await tick();
    const pending = client.command('params.set', {parameters: {n: 3}});
    const rejected = expect(pending).rejects.toThrow(/outcome.*unknown/i);
    socket.close(1013); await rejected;
    await vi.advanceTimersByTimeAsync(1000);
    const next = sockets[1]!; next.open();
    expect(next.sent).toHaveLength(1);
    next.message({v: 1, type: 'state', event_seq: 5, body: {...state, revision: {...revision, number: 2}}});
    await tick(); expect(client.state?.revision?.number).toBe(2);
    client.dispose(); vi.useRealTimers();
  });
  it('fences old socket state and stops retrying invalid authentication', async () => {
    vi.useFakeTimers();
    const {client, socket, sockets} = setup();
    socket.close(1008); await vi.advanceTimersByTimeAsync(60000);
    expect(sockets).toHaveLength(1); expect(client.status).toBe('authentication_required');
    socket.message({v: 1, type: 'state', event_seq: 10, body: state}); await tick();
    expect(client.state).toBeNull(); client.dispose(); vi.useRealTimers();
  });
  it('ignores old notifications and rejects nonfinite command data locally', async () => {
    const {client, socket} = setup();
    socket.message({v: 1, type: 'state', event_seq: 4, body: state}); await tick();
    socket.message({v: 1, type: 'state', event_seq: 3, body: {...state, revision: null}}); await tick();
    expect(client.state?.revision).toEqual(revision);
    await expect(client.command('params.set', {parameters: {n: NaN}})).rejects.toThrow(/finite/);
    client.dispose();
  });
});

it('reads bounded authenticated references and rejects substituted bytes or routes', async () => {
  const bytes = new TextEncoder().encode('{"hello":"world"}');
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  const fetcher = vi.fn(async () => new Response(bytes));
  const client = new StudioClient('secret', {origin: 'http://127.0.0.1:8000', fetch: fetcher});
  const ref = {id: hash, sha256: hash, length: bytes.length, url: `/api/references/${hash}`};
  await expect(client.reference(ref)).resolves.toEqual({hello: 'world'});
  expect(fetcher.mock.calls[0]).toBeDefined();
  await expect(client.reference({...ref, url: 'https://elsewhere.example/'})).rejects.toThrow(/reference/);
  await expect(client.reference({...ref, length: 1})).rejects.toThrow(/length/);
  await expect(client.reference({...ref, length: bytes.length + 1})).rejects.toThrow(/length/);
  fetcher.mockImplementation(async () => new Response(new Uint8Array(bytes.length)));
  await expect(client.reference(ref)).rejects.toThrow(/hash/);
  client.dispose();
});

it('bounds outstanding commands and reports uncertain timeouts', async () => {
  vi.useFakeTimers();
  const {client, socket} = setup();
  socket.message({v: 1, type: 'state', event_seq: 1, body: state}); await tick();
  const pending = Array.from({length: 32}, () => expect(client.command('run.start')).rejects.toThrow(/unknown/));
  await expect(client.command('run.start')).rejects.toThrow(/full/);
  await vi.advanceTimersByTimeAsync(30000); await Promise.all(pending);
  client.dispose(); vi.useRealTimers();
});

it('drops a delayed state reference from a closed socket', async () => {
  const bytes = new TextEncoder().encode(JSON.stringify(state));
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  let finish!: (response: Response) => void;
  const socket = new Socket();
  const client = new StudioClient('secret', {origin: 'http://127.0.0.1:8000',
    socket: () => socket as unknown as WebSocket,
    fetch: () => new Promise(resolve => {finish = resolve;}),
  });
  client.connect(); socket.open();
  socket.message({v: 1, type: 'state', event_seq: 1, body: {ref: {id: hash, sha256: hash, length: bytes.length, url: `/api/references/${hash}`}}});
  socket.close(1008); finish(new Response(bytes));
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(client.state).toBeNull(); client.dispose();
});

it('uploads layouts with a captured revision and keeps conflicts explicit', async () => {
  const socket = new Socket();
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ok: true}));
  const client = new StudioClient('secret', {origin: 'http://127.0.0.1:8000', fetch: fetcher, socket: () => socket as unknown as WebSocket});
  await expect(client.writeLayout({}, revision)).rejects.toThrow(/synchron/);
  client.connect(); socket.open(); socket.message({v: 1, type: 'state', event_seq: 1, body: state}); await tick();
  await expect(client.writeLayout({bad: NaN}, revision)).rejects.toThrow(/finite/);
  await expect(client.writeLayout({}, revision)).resolves.toEqual({ok: true});
  const [url, options] = fetcher.mock.calls[0]!;
  expect(String(url)).toBe('http://127.0.0.1:8000/api/layout');
  expect(options?.headers).toMatchObject({'X-Simulatte-Revision': JSON.stringify(revision), 'X-Simulatte-Session': 'secret'});
  fetcher.mockImplementation(async () => Response.json({error: 'revision_conflict'}, {status: 409}));
  await expect(client.writeLayout({}, revision)).rejects.toThrow(/keep your draft/);
  client.dispose();
});

it('turns an upgrade denial into a fresh-link instruction instead of endless retries', async () => {
  vi.useFakeTimers();
  const socket = new Socket();
  const fetcher = vi.fn<typeof fetch>(async () => new Response(null, {status: 403}));
  const client = new StudioClient('old-secret', {origin: 'http://127.0.0.1:8000', fetch: fetcher,
    socket: () => socket as unknown as WebSocket});
  client.connect(); socket.close(1006); await tick();
  expect(client.status).toBe('authentication_required');
  expect(fetcher.mock.calls[0]?.[1]?.method).toBe('HEAD');
  await vi.advanceTimersByTimeAsync(30000);
  expect(client.status).toBe('authentication_required');
  client.dispose(); vi.useRealTimers();
});

it('preserves finite float parameters outside the safe integer range as JSON floats', async () => {
  const {client, socket} = setup();
  socket.message({v: 1, type: 'state', event_seq: 1, body: state}); await tick();
  const send = vi.spyOn(socket, 'send');
  const pending = client.command('params.set', {parameters: {scale: 1e20}});
  const raw = send.mock.calls[0]?.[0];
  expect(raw).toContain('"scale":1e+20');
  const id = socket.sent.at(-1)!.id;
  socket.message({v: 1, type: 'response', id, body: {ok: true}});
  await expect(pending).resolves.toEqual({ok: true}); client.dispose();
});
