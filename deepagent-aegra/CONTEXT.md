# Deepagent Aegra

An independent deepagents multi-agent demo, self-hosted on aegra: an orchestrator delegates to `file-reader`, `output-writer`, `web-search`, and `mcp` subagents to read uploaded files, produce downloadable output files, and reach connected MCP servers, with no code-execution sandbox.

## Language

### File references

**Attachment**:
A reference to a user-uploaded file, carried as `{key, filename}` in a run's `attachments` state-field list — not the file's content. The content lives on local disk, addressable by `key` through the upload/download HTTP app. `file-reader` resolves an Attachment's `key` to pick a per-format tool by `filename`'s extension.
_Avoid_: treating an Attachment as inline file content (that's deepagents' own built-in `files` channel, which this project deliberately does not use for uploads — see ADR-0001); "upload" as a noun for the reference itself (an upload is the act; the reference is the Attachment)

**Output**:
A reference to a file `output-writer` produced during a run, carried as `{key, filename}` in the `outputs` state-field list — the same shape as an Attachment, for the same reason (a pointer, not inline content). Discovered by `agent-chat-ui` via a post-run fetch of the thread's state, not by parsing the orchestrator's chat reply or polling a separate listing endpoint.
_Avoid_: "artifact", "deliverable" — reserve "Output" as the one term

**Key**:
The opaque identifier the upload/download HTTP app issues for a file on local disk; the only thing an Attachment or Output actually carries besides `filename`. Not a filesystem path — callers never construct or parse it, only pass it back to the HTTP app.
_Avoid_: "path", "id" (too generic — always say "Key")

**Connection**:
A persisted record that the curated GitHub MCP server has been authenticated and is available to the orchestrator — its stored credentials plus its enabled/disabled state, held in the Connection Store. Established once via the auth window in agent-chat-ui's Settings surface. Unlike `web-search`'s per-run toggle, a Connection's enabled state persists across runs until switched off (see ADR-0008). Modeled to hold more than one Connection (a second curated server was researched and deferred — see CONTEXT.md history / issue #22 — not foreclosed, just not built yet).
_Avoid_: "integration" (too vague), "session" (a Connection outlives any single run)

**Connection Store**:
The local, encrypted-at-rest file holding every Connection's credentials and enabled state. Deliberately not a database or secrets manager — sized for a single self-hosted user, not multi-tenancy (see ADR-0008).
_Avoid_: "vault", "secrets manager" — those imply multi-tenant machinery this deliberately isn't

**Registry Entry**:
The pinned `server.json` record for the curated GitHub MCP server (`io.github.github/github-mcp-server`), fetched from the official MCP Registry (registry.modelcontextprotocol.io) and used as the source of truth for what credential fields a Connection needs — read by both agent-chat-ui (to render the auth window's fields) and deepagent-aegra (to know what to store and pass to the MCP client). Pinned to a specific known-good server name, never resolved by a live search, since the Registry is open to anyone to publish to (see ADR-0009).
_Avoid_: "manifest", "server config" — Registry Entry ties it explicitly back to the external Registry as the source

**Auth Mode**:
How a Connection's credentials get collected. Currently just **Credential Form** (paste a static secret — a GitHub PAT — no browser involved), used for the GitHub Connection. A second mode, **Device Code** (the auth window shows a short code and a verification URL, then polls until the person finishes signing in elsewhere), was designed for a since-deferred Microsoft 365 connection and would revive as a second content rendering of the same auth-window modal if that comes back.
_Avoid_: "OAuth" alone for Device Code — both modes are ultimately backed by app credentials of some kind; Device Code specifically names the interactive polling flow.

### Roles

**Orchestrator**:
The top-level deepagents agent a run addresses directly; delegates to `file-reader`, `output-writer`, and `web-search` via the `task` tool and is responsible for acknowledging received Attachments by name in its first reply (the mitigation for LangGraph's silent-drop-on-unrecognized-state-key behavior).

**file-reader**:
The subagent that resolves an Attachment's Key to file content and extracts from it, dispatching to a per-format tool (pdf/xlsx/txt/pptx/docx) chosen by the Attachment's filename extension.

**output-writer**:
The subagent that produces a deliverable file during a run and registers it as an Output (writing to local disk via the upload/download HTTP app and appending `{key, filename}` to the `outputs` state field).

**web-search**:
The subagent that answers one research question from the web (Tavily search plus page fetch), used for anything beyond a single trivial fact. Always registered on the orchestrator's `task` tool, like `file-reader` and `output-writer`, but its actual availability toggles per run: `WebSearchGateMiddleware` reads `configurable.enable_web_search` and refuses the delegation before it reaches this subagent when the flag is off, rather than this subagent ever being left out of the roster itself (see ADR-0007).
_Avoid_: describing `web-search` as "enabled"/"disabled" as a deployment property — it is a per-run toggle, set by the person on the client, not a server-side setting like `TAVILY_API_KEY`'s presence.

**mcp**:
The subagent that exposes every currently-enabled Connection's tools to the orchestrator. Unlike `file-reader`/`output-writer`/`web-search` (fixed, structurally-registered tool sets), `mcp` builds one `fastmcp.ClientGroup` fresh at the start of each run — scoped to whichever Connections are enabled at that moment — and tears it down at the run's end.
_Avoid_: a per-server subagent (no separate `github`/`sharepoint`/`teams` subagents) — one subagent aggregates all enabled Connections, per `ClientGroup`'s own design.
