# Deepagent Aegra

An independent deepagents multi-agent demo, self-hosted on aegra: an orchestrator delegates to `file-reader`, `output-writer`, and `web-search` subagents to read uploaded files and produce downloadable output files, with no code-execution sandbox.

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
