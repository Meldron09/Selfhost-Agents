# A Skill UI is a sandboxed static bundle with a three-message contract, not generative UI

A Skill's `ui/` folder is a static web bundle (entry `ui/index.html`, hand-written or built with e.g. Vite `base: './'`) that a technical author ships ahead of time and a non-technical person installs by uploading a zip. `agent-chat-ui` shows it in an `<iframe sandbox>` **without** `allow-same-origin`, served from a backend route that sets `Access-Control-Allow-Origin: *` (a sandboxed frame has an opaque origin, so its own module scripts and assets load cross-origin).

## Why not the generative-UI path

`agent-chat-ui` already carries the stock LangGraph generative-UI plumbing (`LoadExternalComponent`, `ui` state, the artifact panel), but nothing on the backend emits it: no `push_ui_message`, no `ui` key in `aegra.json`, and it was never verified that Aegra serves the UI-bundle endpoint at all. Going that way would put an unproven server feature on the critical path, and would tie the UI to the model's run rather than to the Skill. The iframe route needs only a static-file route like the existing upload/download app (ADR-0004).

## Sandbox is unconditional

Uploaded HTML/JS runs in the browser, so the sandbox applies whatever the trust level: no `allow-same-origin`, no `localStorage`/cookies, relative assets only. The UI cannot call the API, read other files, or reach the Connection Store.

**The `sandbox` attribute alone does not cut off the network** (verified in Chromium, see the spike in `prototypes/skill-ui-sandbox/`): a sandboxed frame with an opaque origin could still `fetch` the API, because the API answers cross-origin requests. Every response of the `ui/` route therefore also carries `Content-Security-Policy: default-src 'self'; connect-src 'none'; form-action 'none'; img-src 'self' data:`, which blocks `fetch`/XHR/WebSocket and form posts. The iframe uses `sandbox="allow-scripts"` only (no `allow-forms`, no top-navigation). With that header the same bundle still loads its module scripts and assets, since `'self'` resolves from the document URL, not the opaque origin.

Verified in a real browser (host page and `ui/` route on different origins): a Vite/React build with `base: './'` loads its module script and an imported asset; `postMessage` round-trips in both directions with `origin === "null"` (the host must identify the frame by `event.source`, not by origin); a `File` object in `submit` arrives as a real `File`; `localStorage` and `document.cookie` are blocked. Without `Access-Control-Allow-Origin: *` the module script is refused with a CORS error, so that header is required, not optional.

## Three-message contract

The iframe talks to the host over `postMessage` only:

- UI to host: `submit({ fields, files })`. `files` carries `File` objects; the host uploads them through `POST /files`, enforces the extension allowlist and size limits, and turns them into Attachments.
- Host to UI: `status` events (queued, running, done, failed).
- Results are not rendered by the UI. The host shows a standard result panel (final message, Output links, any pending approval).

The contract is documented on one page with a ~30-line copy-paste helper; no package is published. Add message types only when a real Skill needs one.

## Rejected

- **A declarative form spec (`ui.json`)**: safest and simplest, but the author wanted arbitrary UI per Skill.
- **LLM-generated UI from a prose description**: non-deterministic, and unreliable on a small local model.
- **Unsandboxed in-app components**: one buggy or hostile bundle could read the app's own API.
