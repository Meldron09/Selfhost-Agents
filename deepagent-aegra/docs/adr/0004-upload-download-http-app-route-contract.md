# Upload/download HTTP app: `/files` route contract, extension-carrying opaque keys

The new upload/download HTTP app mounts at `/files`, unprefixed at the app root — mirroring `agent-runtime`'s `/artifacts` app — and exposes exactly two routes, no listing endpoint:

- `POST /files` — upload. `multipart/form-data`, one file per request. Response: bare `{"key": "..."}`, nothing echoed back (the caller already has `filename` and `size`; it just picked the file).
- `GET /files/{key}` — download. Raw bytes; `Content-Type` guessed from the key's own extension via `mimetypes.guess_type`; `Content-Disposition: attachment; filename="<key>"`; 404 if the key doesn't resolve to a file on disk.

Confirmed by source-level inspection of the installed `aegra-cli`==0.10.5 package (`aegra_api`, pinned in `agent-runtime/aegra-host/requirements.txt`) that `/files` collides with none of aegra's own reserved top-level prefixes (`/assistants`, `/threads`, `/runs`, `/store`, `/health`/`/ready`/`/live`/`/info`, `/docs`/`/redoc`/`/openapi.json`).

## Key format

A `key` is server-generated at upload time as `"<uuid4><ext>"` (e.g. `"a1b2c3d4.xlsx"`), taking the extension straight from the uploaded file's own name, and is used verbatim as the on-disk filename under the dedicated volume from ADR-0002 (`data/{key}`) — a direct path lookup, never a glob. This stays consistent with CONTEXT.md's "Key" definition (opaque; not a path; never constructed or parsed by a caller): `resolve_attachment_bytes(key)` and `store_output_bytes(filename, bytes) -> key` (the helpers `file-reader`'s and `output-writer`'s prototypes, issues #4/#5, already assume) pass the whole string through without inspecting it. The extension riding along changes nothing about how callers treat it — they don't happen to see a `/` in it, but they still never parse it.

**Rejected**: a bare uuid key (no extension) with the store resolving `data/{key}.*` via glob at download time. Rejected for needless complexity and a theoretical collision risk (two uploads sharing a uuid stem with different extensions) against no real benefit — the extension-carrying key is no less opaque to callers, just simpler to resolve.

## Content-Disposition uses the store-internal name, not the original filename

The download response's `Content-Disposition` header carries the store-internal name (identical to the key, e.g. `attachment; filename="a1b2c3d4.xlsx"`) — mirroring `agent-runtime`'s `download_artifact` exactly (`key.rsplit("/", 1)[-1]`) — not the original human-readable filename the user uploaded.

**Rejected**: persisting the true original filename alongside the bytes (a sidecar file, or a `data/{key}__{original_filename}` naming scheme) so the header could carry it. Rejected because nothing reads it: `agent-chat-ui` already holds the real `filename` from the Output's `{key, filename}` tuple in graph state (ADR-0001) and sets its own `<a download="...">` attribute when rendering the link. The header value is only a sane fallback for something hitting the URL directly (e.g. `curl`), not a source of truth — tracking a second filename server-side for a value nothing in this design consumes was rejected as state the store doesn't need.

## No upload-time validation, no listing endpoint

The app accepts any bytes at any size with no extension allowlist — duplicating `file-reader`'s already-decided dispatch-by-extension / short-circuit-on-unsupported-type logic (issue #4) here would drift the two checks apart over time. There is no `GET /files` listing route: Output discovery already goes exclusively through the post-run `GET /threads/{id}/state` fetch (ADR-0001), so a listing endpoint would be a second, unused discovery path.

## Structural rules carried over unchanged

Every handler is a plain `def` (never `async def` — routes run on aegra's own event loop, and file I/O here is blocking); the app adds no middleware of its own; it exports the same factory shape (`create_app() -> FastAPI`) mounted through the existing, unmodified `aegra-host/http_app_adapter.py` (issue #6 already decided that adapter needs zero code changes — only the target module changes).

Resolved while working wayfinder ticket [Decide: upload/download HTTP route contract (paths, request/response shapes)](https://github.com/Meldron09/Selfhost-Agents/issues/10).
