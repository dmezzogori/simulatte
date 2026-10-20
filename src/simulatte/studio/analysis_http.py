"""Authenticated, bounded HTTP readers over saved analysis outputs; analysis code never runs here.

Routes (``GET``/``HEAD``, session cookie and header like every ``api/*`` route):

- ``api/analysis/<execution>/outputs/<output>/rows?cursor=&limit=&sort=&filter=``: one tagged-JSON page
  (limit 1–500, at most 1 MiB) through ``simulatte.analysis.table_query``. Sorting and filtering build a
  derived index under ``<store>/analysis-cache`` in a contained child, bounded by its timeout. Identical
  concurrent requests share one build, which stops once every waiting client has disconnected.
- ``api/analysis/<execution>/outputs/<output>/csv``: the whole table, streamed as an attachment.
- ``api/analysis/<execution>/artifacts/<path>``: one file listed in the execution's output manifest,
  hash-verified, as an isolated attachment.

Only succeeded executions expose outputs; any other state answers 409 with its diagnostics.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path, PurePosixPath
import queue
import re
import threading

from starlette.responses import Response, StreamingResponse

from simulatte.analysis import codec
from simulatte.analysis.datasets import DatasetManifest, DatasetReader
from simulatte.analysis.store import AnalysisStore
from simulatte.analysis.table_query import TableQueryCancelled, parse_query, query_table
from simulatte.studio.auth import artifact_headers, response_headers
from simulatte.studio.http import ImmutableFile, RangeSource, parse_range

CSV_CHUNK = 64 << 10
MAX_TABLE_MANIFEST = 4 << 20  # the dataset manifest bound admission applies to evidence
EXECUTION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DIGEST = re.compile(r"[0-9a-f]{64}")
ROW_PARAMETERS = frozenset(("cursor", "limit", "sort", "filter"))
DISCONNECT_POLL = 0.5  # seconds between client disconnect checks while a table index builds


class Refused(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status, self.body = status, {"error": error, **extra}


def _json(body, status=200):
    return Response(
        json.dumps(body, allow_nan=False, separators=(",", ":")),
        status_code=status,
        media_type="application/json",
        headers=response_headers(),
    )


def _target(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or path.as_posix() != relative:
        raise Refused(404, "Unknown analysis artifact")
    return root.joinpath(*path.parts)


def _published(server, identity):
    store = server.service.store or server.service.reader
    if store is None or getattr(store, "version", 4) < 4:
        raise Refused(404, "Saved analyses are unavailable")
    analysis = AnalysisStore(store)
    try:
        state = analysis.execution(identity).state
    except KeyError:
        raise Refused(404, "Unknown analysis execution") from None
    if state != "succeeded":
        raise Refused(
            409,
            f"Analysis outputs are available only after success; this execution is {state}",
            state=state,
            details=codec.to_json(analysis.get(identity)["details"]),
        )
    return analysis.output(identity), Path(store.root).resolve() / "analysis-results" / identity


def _table(server, identity, output_id):
    manifest, root = _published(server, identity)
    output = next((o for o in manifest["outputs"] if o["id"] == output_id), None)
    if output is None or output["kind"] != "table":
        raise Refused(404, "Unknown table output")
    prefix = output["name"] + "/table/"
    listed = {a["path"]: a for a in output["artifacts"]}
    try:
        descriptor = listed[prefix + "manifest.json"]
        if descriptor["length"] > MAX_TABLE_MANIFEST:
            raise ValueError("Table manifest exceeds 4 MiB")
        source = ImmutableFile(_target(root, descriptor["path"]), descriptor["length"], descriptor["sha256"])
        with source.open() as (file, _):
            dataset = DatasetManifest.from_dict(json.loads(file.read()))
        for chunk in dataset.chunks:
            path = prefix + chunk.path
            if listed.get(path) != {"path": path, "length": chunk.length, "sha256": chunk.sha256}:
                raise ValueError("Table chunk is not a listed artifact")
        return output, DatasetReader(root / output["name"] / "table", dataset)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise Refused(404, "Saved table unavailable: " + str(error)[:1024]) from None


def _row_query(request):
    items = request.query_params.multi_items()
    keys = [key for key, _ in items]
    if len(set(keys)) != len(keys) or not set(keys) <= ROW_PARAMETERS:
        raise Refused(400, "Table pages accept cursor, limit, sort and filter, each at most once")
    values = dict(items)
    limit = values.get("limit", "100")
    if not limit.isdecimal() or str(int(limit)) != limit or not 1 <= int(limit) <= 500:
        raise Refused(400, "Page limit must be 1–500")
    return values.get("cursor"), int(limit), values.get("sort") or None, values.get("filter") or None


def _rows(server, identity, output_id, cursor, limit, sort, filter, cancel=None):
    _, reader = _table(server, identity, output_id)
    store = server.service.store
    # Derived indexes only; never inside analysis-results. A recovery reader writes nothing.
    cache = Path((store or server.service.reader).root).resolve() / "analysis-cache"
    try:
        query = parse_query(sort, filter, reader.manifest.schema)
        if not query.empty:
            if store is None:
                raise Refused(409, "Sorting and filtering need a writable project; page the table unsorted")
            cache.mkdir(exist_ok=True)
        return query_table(reader, query, cursor, limit=limit, cache_dir=cache, cancel=cancel).to_dict()
    except ValueError as error:
        raise Refused(400, str(error)[:4096]) from None
    except (TimeoutError, TableQueryCancelled) as error:
        raise Refused(503, "Table index unavailable; retry the request: " + str(error)[:1024]) from None


def _artifact(server, identity, relative):
    manifest, root = _published(server, identity)
    for output in manifest["outputs"]:
        for descriptor in output["artifacts"]:
            if descriptor["path"] == relative:
                media = (
                    output["metadata"]["media_type"]
                    if output["kind"] == "image"
                    else "application/json"
                    if relative.endswith(".json")
                    else "application/octet-stream"
                )
                file = ImmutableFile(_target(root, relative), descriptor["length"], descriptor["sha256"])
                return RangeSource([file], descriptor["length"]), media
    raise Refused(404, "Unknown analysis artifact")


class _Stopped(Exception):
    pass


def csv_chunks(reader):
    """Stream ``reader.to_csv`` through a bounded queue; closing the stream stops the writer."""
    chunks = queue.Queue(maxsize=4)
    stopped = threading.Event()

    def put(item):
        while not stopped.is_set():
            try:
                chunks.put(item, timeout=0.1)
                return
            except queue.Full:
                pass
        raise _Stopped

    class Sink:
        def __init__(self):
            self.parts, self.size = [], 0

        def write(self, text):
            self.parts.append(text)
            self.size += len(text)
            if self.size >= CSV_CHUNK:
                self.flush()

        def flush(self):
            if self.parts:
                put("".join(self.parts))
                self.parts, self.size = [], 0

    def produce():
        try:
            sink = Sink()
            reader.to_csv(sink)
            sink.flush()
            item = None
        except Exception as error:
            item = error
        try:
            put(item)
        except _Stopped:
            pass

    threading.Thread(target=produce, name="simulatte-analysis-csv", daemon=True).start()
    try:
        while (item := chunks.get()) is not None:
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stopped.set()


async def _watch(request, disconnected):
    """Set ``disconnected`` once the client has gone; the index build thread polls it as ``cancel``."""
    while not await request.is_disconnected():
        await asyncio.sleep(DISCONNECT_POLL)
    disconnected.set()


async def handle(server, request, path):
    if not server.headers_valid(request) or not server.authenticated(request):
        return _json({"error": "Open a fresh terminal link"}, 403)
    pieces = path.split("/", 4)
    try:
        if len(pieces) != 5 or EXECUTION.fullmatch(pieces[2]) is None:
            raise Refused(404, "Unknown analysis resource")
        identity, kind, rest = pieces[2:]
        output_id, _, view = rest.partition("/")
        if kind == "outputs" and DIGEST.fullmatch(output_id) and view == "rows":
            query = _row_query(request)
            disconnected = threading.Event()
            watcher = asyncio.create_task(_watch(request, disconnected))
            try:
                return _json(
                    await asyncio.to_thread(_rows, server, identity, output_id, *query, cancel=disconnected.is_set)
                )
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        if request.url.query:
            raise Refused(404, "Unknown analysis resource")
        if kind == "outputs" and DIGEST.fullmatch(output_id) and view == "csv":
            output, reader = await asyncio.to_thread(_table, server, identity, output_id)
            headers = artifact_headers("text/csv; charset=utf-8")
            headers["Content-Disposition"] = f'attachment; filename="{output["name"]}.csv"'
            return StreamingResponse(csv_chunks(reader), headers=headers)
        if kind != "artifacts":
            raise Refused(404, "Unknown analysis resource")
        source, media = await asyncio.to_thread(_artifact, server, identity, rest)
        headers = artifact_headers(media)
        try:
            start, end, status = parse_range(request.headers.get("range"), source.length)
        except ValueError:
            headers["Content-Range"] = f"bytes */{source.length}"
            return Response(status_code=416, headers=headers)
        try:
            await asyncio.to_thread(source.validate, start, end)
        except (ValueError, OSError):
            raise Refused(404, "Saved analysis artifact unavailable") from None
        headers.update({"Content-Length": str(end - start + 1), "Accept-Ranges": "bytes"})
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{source.length}"
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers)
        return StreamingResponse(source.chunks(start, end), status_code=status, headers=headers)
    except Refused as error:
        return _json(error.body, error.status)
