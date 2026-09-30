# Verify fastmcp / langchain.mcp client + ClientGroup API against the installed version

Research for [issue #21](https://github.com/Meldron09/Selfhost-Agents/issues/21) (child of the MCP support map, issue #19). It feeds the "Design the `mcp` subagent + Connection Store" ticket. The earlier research on `fastmcp`'s client API was written from general knowledge because `fastmcp` wasn't installed. This one checks the installed package.

**Sources (primary only).** The installed package source under `deepagent-aegra/.venv/lib/python3.12/site-packages/` (cited below as `site-packages/...`), the official FastMCP docs at gofastmcp.com, the LangChain docs at docs.langchain.com, and GitHub's own `github/github-mcp-server` README. Every behavioural claim that says **(ran)** was executed in scratch scripts in a temp directory. They are not in the repo; the key snippets are inlined below. The scripts used a tiny FastMCP test server over real stdio subprocesses, a real local HTTP server, and in-process fakes. No real credentials or GitHub traffic were used.

## Installed versions (recorded first)

Read from `importlib.metadata` in `deepagent-aegra/.venv`, Python 3.12.13, on 2026-09-30.

| Package | Version | Note |
|---|---|---|
| `fastmcp` | **4.0.10** | pulled in by `langchain[mcp]>=1.3` (`deepagent-aegra/requirements.txt`) |
| `fastmcp-slim` | 4.0.10 | the `fastmcp.client.*` modules ship from here |
| `langchain` | **1.4.2** | contains the `langchain.mcp` namespace (beta warning on import) |
| `langchain-core` | 1.6.4 | |
| `mcp` (MCP Python SDK) | **2.2.0** | SDK **v2**; `mcp-types` 2.2.0 |
| `deepagents` | 0.7.18 | |
| `langgraph` | 1.2.12 | |
| `aegra-cli` / `aegra-api` | 0.10.5 | |
| `httpx2` / `httpcore2` | 2.13.1 | FastMCP 4 uses `httpx2`, not `httpx` |

**Correction to the ticket's framing:** there is no separate `langchain-mcp-adapters` package here. `langchain.mcp` is the adapter layer, at `site-packages/langchain/mcp/{__init__,adapter,tools,elicitation}.py`. Its public surface is `MCPAdapter`, `as_langchain_tool` and `MCPToolArtifact` (`langchain/mcp/__init__.py:19-20,32-36`). The LangChain docs page for MCP documents `MCPAdapter`, not `MultiServerMCPClient` (https://docs.langchain.com/oss/python/langchain/mcp).

## Summary of answers

- **Q1. Constructors.** `ClientGroup` **does exist** in fastmcp 4.x, at `fastmcp.client.group.ClientGroup` (not re-exported from top-level `fastmcp`).
  - Signature: `ClientGroup(clients: Mapping[str, Client])`, plus `ClientGroup.from_config(config, *, default_mode="auto")`. Each dict key is a server name and becomes the tool-name prefix.
  - There is also a separate multi-server mechanism, `Client(MCPConfig | dict)`, which is a proxy-composite and **not** a `ClientGroup`.
  - stdio: `StdioTransport(command, args, env=None, cwd=None, keep_alive=None, log_file=None)`.
  - HTTP: `StreamableHttpTransport(url, headers=None, auth=None, httpx_client_factory=None, verify=None)`. A bare string `auth=` becomes `Authorization: Bearer <token>`.
  - MCPConfig dict entries carry the same fields (`command/args/env/keep_alive`, `url/headers/auth`).
- **Q2. Cost and lifecycle.** Construction is cheap and does no I/O. **(ran)** `Client(...)` takes about 0.1-0.4 ms (the first call in a process took 27 ms for lazy imports), and no subprocess is spawned. The real cost is `__aenter__`: about 0.56-0.64 s for a Python stdio server **(ran)**. Open-per-run and close-per-run is a supported pattern: the context managers are reentrant, reference-counted and reconnectable, and the same `ClientGroup` instance can be entered repeatedly **(ran)**.
  - **Correction / gotcha:** `StdioTransport.keep_alive` **defaults to `True`**. Leaving the `async with` block does **not** kill the stdio subprocess. **(ran)** The child stayed alive until `client.close()`. Use `keep_alive=False` (or `"keep_alive": false` in an MCPConfig entry), or call `close()` on every member client. `ClientGroup` has **no** `close()`.
- **Q3. Tool conversion.** The call is `await MCPAdapter(group_or_client_or_config).list_tools()` on a connected-or-not adapter; it returns `list[StructuredTool]`. Under the hood this is `await as_langchain_tool(mcp_tool, client_or_group)` per tool (`langchain/mcp/adapter.py:221-242`, `langchain/mcp/tools.py:234-304`).
  - The tools **stay callable after the `async with` block exits**, but only because **each call does its own `async with client:`**. **(ran)** If no outer connection is held, every tool call **reconnects the whole group**. With `keep_alive=False` stdio, that re-spawned the subprocess on every call (spawns went 1 to 4 over 3 calls).
  - To get one connection per run, **make the tool calls inside the `async with adapter:` block.** **(ran)** Five concurrent calls inside the block shared one subprocess.
  - The tools are **async-only** (`coroutine=` set, no `func`). Conversion and use were verified **(ran)** inside a real `create_agent` / `create_deep_agent` run.
- **Q4. Mixed groups.**
  - stdio and HTTP **mix in one `ClientGroup`** **(ran)**, because each member keeps its own transport. `Client(MCPConfig)` also mixes them.
  - Tool names are `f"{server_key}_{upstream_name}"` (`group.py:206`). A collision raises `ValueError` from `list_tools()` and aborts tool discovery **(ran)**.
  - **Failure semantics differ by mechanism.**
    - `ClientGroup` is **all-or-nothing**: one member failing to connect fails `async with group` (`group.py:118-125`). **(ran)** The healthy members are cleaned up; no subprocess was left behind.
    - `Client(MCPConfig multi)` and `MCPAdapter(dict_config)` are **partial-tolerant**: a bad server is logged as a warning and skipped, and only "all failed" raises (`transports/config.py:260-280`). **(ran)**
  - **Timeouts.** `init_timeout` defaults to `None`, meaning **no timeout**. **(ran)** A hung server blocks `connect` forever unless `init_timeout=` is set on each `Client`. Per-request `timeout` also defaults to `None`.
- **Also relevant.** `SubAgent["tools"]` is bound at build time, but the graph is built once per process. For a per-run group, use a `CompiledSubAgent` whose `runnable` opens the group itself. That was verified **(ran)** with a real `create_deep_agent`. See the extras section.

### Surprises vs prior assumptions (short list)

1. `ClientGroup` lives in `fastmcp.client.group`, and the real LangChain bridge is `MCPAdapter`. There is no `langchain-mcp-adapters` and no `MultiServerMCPClient`.
2. stdio `keep_alive` defaults to **True**: leaving the `async with` block does not stop the subprocess. The "open at run start, close at run end" claim is **only true** with `keep_alive=False` or an explicit `close()`.
3. Tools "stay usable after the context exits" (LangChain docs), but by **reconnecting per call**, not by holding a live session.
4. `ClientGroup` is all-or-nothing on connect. The partial-tolerant path is `Client(MCPConfig)` or a manual probe.
5. The default `init_timeout` is `None` (hangs forever).
6. A 401 from an HTTP member surfaces as a generic `MCPError(-32603, "Server returned an error response")` with no status code. **(ran)** against a local fake that returns 401.

---

## Q1. Exact constructor signatures and how connection params and auth are passed

### `fastmcp.Client`

Source: `site-packages/fastmcp/client/client.py:413-445`.

```python
Client(transport, name=None, roots=None, sampling_handler=None, sampling_capabilities=None,
       elicitation_handler=None, log_handler=None, message_handler=None, progress_handler=None,
       timeout=None, auto_initialize=True, init_timeout=None, client_info=None,
       auth=None,            # httpx2.Auth | "oauth" | str (bearer token) | None  (line 437)
       verify=None,          # HTTP transports only; ValueError otherwise (465-485)
       mode="auto",          # "auto" | "legacy" | a modern version string (439, 450-460)
       prior_discover=None, input_required_max_rounds=..., cache=None, extensions=None, result_claims=None)
```

- `transport` accepts a `ClientTransport`, a `FastMCP` server, a URL string or `AnyUrl`, a `Path`, an `MCPConfig`, or a `dict` (`client.py:413-424`). It is inferred by `infer_transport` (`client.py:463`).
- `auth=` on the `Client` is forwarded to `transport._set_auth(auth)` (`client.py:487-488`). A `str` becomes `BearerAuth(token)` (`transports/http.py:124-125`).
- `BearerAuth` sets `Authorization: Bearer <token>` (`site-packages/fastmcp/client/auth/bearer.py:15-17`). **(ran)** The header was observed on the wire at a local HTTP MCP server.
- `timeout` is the per-request read timeout, and `init_timeout` is the handshake timeout. Their defaults are in the Q4 section.

### stdio: `StdioTransport`

Source: `site-packages/fastmcp/client/transports/stdio.py:33-67`.

```python
StdioTransport(command: str, args: list[str], env: dict[str,str]|None=None,
               cwd: str|None=None, keep_alive: bool|None=None, log_file=None)
```

- Env handling is in the MCP SDK: `site-packages/mcp/client/stdio.py:128` spawns the child with `env=get_default_environment() | (server.env or {})`.
  - The inherited allowlist is only `HOME, LOGNAME, PATH, SHELL, TERM, USER` on POSIX (`mcp/client/stdio.py:40-57`). **The child does NOT inherit the parent's full environment.**
  - **(ran)** A parent-only variable was invisible to the child, and the explicit `env={"GITHUB_PERSONAL_ACCESS_TOKEN": ...}` value was visible.
  - The docs say the same: "Values passed through `env` merge with an inherited allowlist ... Credentials require explicit forwarding" (https://gofastmcp.com/clients/transports).
  - Consequence: anything the command itself needs (`DOCKER_HOST`, `DOCKER_CONFIG`, `XDG_*`, proxy vars) must be passed explicitly in `env`.
- **Mapping to the pinned GitHub entry.**
  - The registry entry's OCI stdio runtime args are `-e GITHUB_PERSONAL_ACCESS_TOKEN={token}` (`docs/research/pin-mcp-registry-entries.md`, section 1 on branch `research/pin-mcp-registry-entries`).
  - GitHub's README passes the token as env var `GITHUB_PERSONAL_ACCESS_TOKEN` (https://github.com/github/github-mcp-server).
  - With `docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ...` (name only, no value), docker forwards the value from the docker CLI's own environment, which is exactly what `StdioTransport(env={...})` supplies. This keeps the secret out of `argv` (visible in `ps`).
  - This is inferred from docker's documented `-e VAR` semantics plus the verified env plumbing. Docker itself was not run here.

Verified snippet (stdio with env, no network):

```python
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

stdio = Client(
    StdioTransport(
        command="docker",
        args=["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
              "ghcr.io/github/github-mcp-server:1.12.2"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": token, "DOCKER_HOST": "..."},  # explicit only
        keep_alive=False,          # see Q2: default True leaves the child running after `async with`
    ),
    name="github", init_timeout=30, timeout=60,
)
assert stdio.transport.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == token and not stdio.is_connected()
```

### HTTP: `StreamableHttpTransport`

Source: `site-packages/fastmcp/client/transports/http.py:34-83`.

```python
StreamableHttpTransport(url: str|AnyUrl, headers: dict[str,str]|None=None,
                        auth: httpx2.Auth|Literal["oauth"]|str|None=None,
                        httpx_client_factory=None, verify=None)
```

- Static headers go in `headers=`, merged into the httpx client at connect time (`http.py:171`).
- **(ran)** Both ways construct fine and differ only in where the secret lives:
  - `auth="ghp_..."` gives `transport.auth = BearerAuth`, `transport.headers == {}`, and the wire header is `Authorization: Bearer ghp_...`.
  - `headers={"Authorization": "Bearer ghp_..."}` gives `transport.auth is None`.
- **Mapping to GitHub's remote entry.**
  - The registry entry declares header `Authorization` as `isSecret` for `https://api.githubcopilot.com/mcp/` (streamable-http).
  - GitHub's README gives the format `Authorization: Bearer <token>` (https://github.com/github/github-mcp-server).
  - If the Connection Store keeps the raw PAT, use `auth=pat`, since `BearerAuth` adds the `Bearer ` prefix. If it keeps the whole header value, use `headers=`; the value must then include `Bearer `.
- `Client("https://...", auth=tok)` with a string URL also works. **(ran)** It infers `StreamableHttpTransport` and sets `BearerAuth`.
- A URL whose path matches `/sse` is inferred as SSE instead (`mcp_config.py:55-72`). `https://api.githubcopilot.com/mcp/` is inferred as streamable HTTP.

Verified snippet (HTTP with bearer token):

```python
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

http = Client(StreamableHttpTransport("https://api.githubcopilot.com/mcp/", auth=token),
              name="github", init_timeout=30, timeout=60)
assert type(http.transport.auth).__name__ == "BearerAuth"
```

### `ClientGroup`

Source: `site-packages/fastmcp/client/group.py:32-93`. Docs: https://gofastmcp.com/clients/client-groups.

```python
from fastmcp.client.group import ClientGroup      # NOT `from fastmcp import ClientGroup`
ClientGroup(clients: Mapping[str, Client])          # ValueError("ClientGroup requires at least one client") if empty
ClientGroup.from_config(config: MCPConfig | dict, *, default_mode="auto")   # one independent Client per server entry
group.clients  # read-only MappingProxyType; membership is fixed at construction
```

- `from_config` builds `Client(server.to_transport(), mode=...)` per server (`group.py:87-91`). It therefore accepts the same MCPConfig entry shapes:
  - stdio: `StdioMCPServer` with `command/args/env/cwd/timeout/keep_alive` (`mcp_config.py:180-222`).
  - remote: `RemoteMCPServer` with `url/transport/headers/auth/sse_read_timeout` (`mcp_config.py:229-291`).
  - `keep_alive` is honoured for stdio entries. **(ran)** `from_config` produced `StdioTransport(keep_alive=False)` for `"keep_alive": false`.
- **Not honoured by `from_config`:** the per-entry `timeout` field (ms) in an MCPConfig entry is parsed but `to_transport()` never passes it, and `from_config` builds `Client(...)` with no `timeout=`/`init_timeout=`. To set timeouts, build the `Client` objects yourself and use the `ClientGroup(dict)` constructor. This is from reading `mcp_config.py:213-222,265-291` and `group.py:91`. The `timeout` field is an unverified non-effect; I did not run a hung-call test against `from_config`.
- Note: `ClientGroup` is explicitly a peer of `Client`, not a transport. `fastmcp.Client(group)` is not valid (`langchain/mcp/adapter.py:67-68`).

Verified snippet (mixed group, construction only, about 0.4 ms):

```python
group = ClientGroup({"github": stdio, "github_remote": http})   # keys = tool-name prefixes
# or
group = ClientGroup.from_config({"mcpServers": {
    "github": {"command": "docker", "args": [...], "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token}, "keep_alive": False},
    "gh_remote": {"url": "https://api.githubcopilot.com/mcp/", "transport": "http", "auth": token},
}})
```

---

## Q2. Construction cost, `connect()` cost, per-run open/close, reentrancy, subprocess fate

**Construction is cheap.** `Client.__init__` only builds handlers and session kwargs (`client.py:446-588`). `StdioTransport.__init__` stores fields and creates anyio primitives (`stdio.py:60-75`). Nothing spawns or connects. **(ran)** `Client(StdioTransport(...))` took 0.37 ms, `Client(StreamableHttpTransport(...))` took 0.14 ms and `ClientGroup.from_config` took 0.41 ms. The process count before and after construction was unchanged, and the server log showed 0 starts.

**The cost is in `async with`.** `__aenter__` calls `_connect()` (`client.py:950-956`). It starts a background `asyncio.Task` (`_session_runner`, `client.py:983-985`) that opens the transport and performs protocol negotiation. **(ran)** The first connect to a Python FastMCP stdio server took 561 ms, and `open + list_tools + close` cycles took about 640 ms each (three cycles). The GitHub docker image was not measured, so its start-up will differ. Plan for container start plus handshake, per run.

**Reentrancy and refcounting (supported by design).**
- The class docstring: "supports reentrant context managers (multiple concurrent `async with client:` blocks) using reference counting and background session management" (`client.py:275-278`).
- `ClientGroup` states it too: "entries are reference counted, the first entry connects every client, and the last exit disconnects them" (`group.py:38-44`; docs https://gofastmcp.com/clients/client-groups).
- **(ran)** A nested `async with c:` took 0.03 ms and spawned nothing.
- **(ran)** The same `ClientGroup` instance can be entered, exited and entered again (two cycles, two fresh subprocesses with `keep_alive=False`).
- The session runs in its **own** `asyncio.Task` (`client.py:983`), not in the task that entered the `async with`. So tool calls from other tasks, which LangGraph's ToolNode does, do not hit anyio cancel-scope task-affinity problems. **(ran)** Five concurrent tool calls from child tasks shared one session.
- Exits are cancellation-tolerant by design. The hold count is released before any `await`, and the close runs in its own shielded task (`client.py:1074-1096`, `group.py:137-151`). This is read from source, not exercised under cancellation.

**Is it meant as a long-lived singleton?** No. The docs' own pattern is `async with client:` scoping (https://gofastmcp.com/clients/client). The group docs call the group context optional and reentrant. A fresh instance per run is fine and **(ran)** cleanly recycled.

**What happens to stdio subprocesses on close:**

| Setup | After `async with` exit | After `client.close()` | Source / evidence |
|---|---|---|---|
| `StdioTransport(keep_alive=None)` (default, resolves to **True**) | **Child still alive** (**ran**) | Child gone (**ran**) | `stdio.py:64-66` (`if keep_alive is None: keep_alive = True`), `stdio.py:91-95` |
| `keep_alive=False` | Child gone (**ran**) | n/a | `stdio.py:92-93` calls `disconnect()` |
| Client object garbage-collected, default keep_alive | `__del__` sets the stop event and the child exited within 0.5 s (**ran**) | n/a | `stdio.py:221-229`. Don't rely on this. |

- The docs confirm it: "STDIO transports maintain sessions across multiple client contexts by default (`keep_alive=True`). This reuses the same subprocess for multiple connections" (https://gofastmcp.com/clients/transports).
- `Client.close()` is `await self._disconnect(force=True); await self.transport.close()` (`client.py:1393-1395`), and `transport.close()` calls `disconnect()` (`stdio.py:218-219`).
- **`ClientGroup` has no `close()`**, only `__aexit__`. With the default `keep_alive`, closing a group leaves its stdio children running. **(ran)** One child stayed alive after `async with adapter` exited, and was gone after `await c.close()` for each `adapter.client.clients.values()`.
- Shutdown of an exiting child follows the MCP spec sequence: close stdin, wait up to `PROCESS_TERMINATION_TIMEOUT = 2.0` s, then SIGTERM/SIGKILL the process tree (`mcp/client/stdio.py:59-63,249-262`). A slow-to-exit docker container can therefore add about 2 s or more to run teardown. `fastmcp.settings.client_disconnect_timeout` (default 5 s) bounds the client-side wait (`site-packages/fastmcp/settings.py:186-191`).

**Recommendation for the `mcp` subagent:** set `keep_alive=False` on every stdio `StdioTransport`, or `"keep_alive": false` in MCPConfig entries. HTTP members need no extra handling, since `StreamableHttpTransport.close()` only resets the captured session id (`http.py:230-232`) and the httpx client is closed by the connect context (`http.py:215-225`).

---

## Q3. How `langchain.mcp` turns a connected group's tools into LangChain tools

### The real API

File: `site-packages/langchain/mcp/adapter.py` (245 lines), with `tools.py` for the conversion.

```python
from langchain.mcp import MCPAdapter, as_langchain_tool       # emits LangChainBetaWarning once per process

MCPAdapter(target)        # target: Client | ClientGroup | ClientTransport | FastMCP | MCPServer | AnyUrl | Path | MCPConfig | dict | str(http/https URL only)
async with adapter: ...   # __aenter__/__aexit__ delegate to the underlying client/group
await adapter.list_tools(*, cache_mode="use") -> list[BaseTool]     # adapter.py:221-242
adapter.client            # the underlying Client / ClientGroup (a CLONE, see below)

await as_langchain_tool(tool: mcp.types.Tool, client: Client | ClientGroup) -> BaseTool   # tools.py:234-304
```

- The `list_tools` body is `async with self: remote = await self._client.list_tools(cache_mode=...); return [await as_langchain_tool(t, self._client) for t in remote]` (`adapter.py:240-242`).
- `as_langchain_tool` returns a `StructuredTool(name=tool.name, description, args_schema=<MCP JSON schema>, coroutine=call_tool, response_format="content_and_artifact", metadata={"mcp": {...}}, handle_tool_error=_handle_mcp_tool_error)` (`tools.py:296-304`).
- For a `ClientGroup` target it resolves the public prefixed name to its owning member client first (`tools.py:276-278`). **(ran)** Names came out as `local_ping`, `remote_ping`, and so on.
- **Constructor gotchas.**
  - `MCPAdapter.__init__` never uses your objects directly. For a `ClientGroup` it builds a **new** `ClientGroup` of `client.new()` clones (`adapter.py:190-192`). `Client.new()` is a `copy.copy` with fresh session state, so **the clone shares the original's transport object** (`client.py:771-776`).
  - Use `adapter.client` (not your original group) if you need to close members.
  - It arms each client for LangGraph `interrupt()`-based elicitation unless the client already has an elicitation handler (`adapter.py:183-188`, `langchain/mcp/elicitation.py`).
  - A bare `str` target must be an http(s) URL, to prevent a string from silently launching a local file (`adapter.py:84-122`).
- **Result conversion.**
  - Text, image and file content blocks are converted (`tools.py:120-165`). Audio content raises `NotImplementedError` (`tools.py:128-133`).
  - Tool-reported failures (`isError=True`) become a `ToolMessage(status="error")` with the server's text, so the model can self-correct. **(ran)** A tool that raised produced `ToolMessage error ... "Error calling tool 'boom': kaboom"` (`tools.py:89-117,168-180`).
  - Transport and conversion failures propagate as exceptions (`tools.py:252-255`).
  - Returned content is a list of LangChain content blocks, e.g. `[{'type': 'text', 'text': '...', 'id': 'lc_...'}]`.
- Tools are **async-only**: only `coroutine=` is set. **(ran)** `t.func is None` for every tool, so a sync `.invoke()` is unsupported and `ainvoke` is required. `aegra` runs graphs with `ainvoke`/`astream`, which matches (ADR-0006 already records that async paths matter here).

### Inside vs outside the `async with` block

**Conversion** (`list_tools`) wraps itself in `async with self:` (`adapter.py:240`), so it works whether or not the caller holds an outer block. **(ran)** Both modes worked.

**Using the tools afterwards is where it matters.** `as_langchain_tool`'s inner `call_tool` does `async with client:` around every call (`tools.py:288-294`). Its docstring: "FastMCP clients are reentrant, so the tool can open the client itself whether or not a connection is already held elsewhere" (`tools.py:240-242`). LangChain's docs agree that "The tools hold the client, so the agent stays usable after the context exits" (https://docs.langchain.com/oss/python/langchain/mcp). What that means in practice, **(ran)** with a real stdio server and a real local HTTP server in one group (`keep_alive=False`):

| Scenario | Result |
|---|---|
| Tools built **and** called inside `async with adapter:` | 1 subprocess spawn; 5 concurrent calls share it; all torn down on block exit (**ran**) |
| Tools built inside the block, called **after** the block exited | Each call **reconnects the whole group**: about 800 ms per call, and a new subprocess each time (spawns 1 to 2 to 3) (**ran**) |
| `tools = await adapter.list_tools()` with **no** outer block | 1 spawn for listing, then 1 more per call (1 to 4 over 3 calls) (**ran**) |
| Same, but `keep_alive` left at default True | Post-exit calls reuse the lingering child (about 17 ms per call, 1 spawn total), but the child is **leaked** until `close()` (**ran**) |

The post-exit path also reconnects **every** group member, including HTTP ones, not only the one being called. `call_tool` enters `client`, and `client` is the group (`tools.py:288` with `client` the `ClientGroup`). The same holds for `MCPAdapter(dict_config)` (**ran**: both backends respawned on a post-exit call).

**Conclusion:** create the adapter and the tools, and **also run the agent, inside one `async with adapter:` scope that spans the subagent's whole run.** Tools must not escape that scope. If they do they "work", but with a silent reconnect or respawn per call and, with the default `keep_alive`, a leaked subprocess.

### Verified end-to-end snippet (run against a real stdio server, and inside `create_deep_agent`)

```python
from fastmcp import Client
from fastmcp.client.group import ClientGroup
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from langchain.mcp import MCPAdapter

group = ClientGroup({
    "github": Client(StdioTransport("docker", [...], env={"GITHUB_PERSONAL_ACCESS_TOKEN": tok}, keep_alive=False),
                     init_timeout=60),
    # "m365": Client(StdioTransport("npx", [...], env={...}, keep_alive=False), init_timeout=60),
    # "remote": Client(StreamableHttpTransport(url, auth=tok), init_timeout=30),
})
async with MCPAdapter(group) as adapter:                 # connects all members concurrently, closes all on exit
    tools = await adapter.list_tools()                   # -> [StructuredTool("github_<tool>"), ...]
    sub = create_agent(model, tools=tools, system_prompt=...)
    out = await sub.ainvoke({"messages": messages}, config)   # tool calls reuse the one connection
```

### In-process evidence, strongest form

The test used real subprocesses rather than the in-memory transport, which is stronger for the subprocess-lifecycle questions. All numbers above come from those runs. `FastMCP` servers also work as adapter targets via the in-memory `FastMCPTransport` (`adapter.py:47-58`). A separate in-process collision probe (Q4) used two `FastMCP` objects directly.

---

## Q4. Aggregating multiple servers: stdio + HTTP mixing, name prefixing, failures, timeouts

### Mixing transports

Yes. A `ClientGroup` holds independent `Client`s, each with its own transport, session, capabilities and protocol version (`group.py:33-36`; docs: "A `ClientGroup` retains one MCP connection per configured server, so legacy and modern servers can operate in their native eras at the same time", https://gofastmcp.com/clients/client-groups). **(ran)** A stdio server and a streamable-HTTP server (with bearer auth) in one group connected, listed 8 tools (4 per server) and executed calls on both. The `creds` probe tool showed the stdio child got its `env` token and the HTTP server got `Authorization: Bearer ghp_HTTP`.

Credentials stay per member: the env var goes only to the stdio child, and the bearer token only to the HTTP member. There is no cross-leakage, because the env is per `StdioTransport` and the auth is per `StreamableHttpTransport`.

### Two different multi-server mechanisms in fastmcp 4 (important)

| | `ClientGroup` | `Client(MCPConfig with >1 server)` (also `MCPAdapter(dict)`) |
|---|---|---|
| What it is | N independent clients plus routing (`group.py`) | One client over a **composite FastMCP router** that mounts a proxy per backend (`transports/config.py:214-288`) |
| Prefix | `{server_key}_{tool}` (`group.py:206`) | `{server_key}_{tool}` via `composite.mount(proxy, namespace=name)` (`config.py:284-286`) |
| One server fails to connect | **Whole `async with` fails** (`group.py:118-125`) | **Skipped with a logged warning**; raises only if all fail (`config.py:260-280`) |
| Protocol era | Per server | One era for all, forced to legacy if any backend is legacy (`config.py:186-204`) |
| Needs server half of fastmcp | No | Yes (`from fastmcp.server.server import FastMCP`, `config.py:148-153`) |
| Per-server `timeout`/`init_timeout` | Yes, if you build the `Client`s | Only client-wide `init_timeout`/`timeout` on the outer `Client` |

### Name prefixing and collisions

- The public tool name is `f"{server_name}_{tool.name}"` (`group.py:206`). The name is the **dict key you pass**, verbatim. **(ran)** The key `"io.github.github"` produced the tool name `io.github.github_b_c`; nothing validates or sanitizes it. **Use short slug keys** such as `github` and `m365`, never the registry's dotted and slashed names. I did not verify provider-side tool-name regexes here; treat "keys must be `[A-Za-z0-9_-]` slugs" as a precaution.
- A collision (`"a"` with tool `b_c` versus `"a_b"` with tool `c`, both becoming `a_b_c`) raises `ValueError("Tool name collision: 'a_b_c'")` from `group.list_tools()` (`group.py:207-208`). **(ran)** This aborts discovery for the whole group. Slug keys that contain no underscore-joined ambiguity avoid it.
- The GitHub server's own tool names are not prefixed by `github` upstream, so the model sees `github_<tool>`. If the `mcp` subagent's prompt names tools, it should use the prefixed names.

### Failure behaviour and partial-failure recipe

All of these are **(ran)** with local fakes standing in for the broken member:

| Failure | `ClientGroup` outcome | Time |
|---|---|---|
| Missing stdio binary | `RuntimeError: Client failed to connect: [Errno 2] No such file or directory: ...`; the healthy member is unwound and **no orphan subprocess** (`group.py:120-125`) | 0.6 s |
| HTTP connection refused | `RuntimeError: Client failed to connect: All connection attempts failed` | 0.6 s |
| HTTP 401 (a server returning 401 to every POST) | `mcp.shared.exceptions.MCPError(-32603, 'Server returned an error response', None)`. **No HTTP status or `WWW-Authenticate` is exposed**, and the `httpx2.HTTPStatusError` passthrough (`client.py:246-247`) did not fire in this SDK. The same error appeared with `mode="auto"` and `mode="legacy"`. | 0.5 s |
| stdio process that never speaks MCP, `init_timeout=2` | `RuntimeError: Client failed to connect: Failed to initialize server session` | 4.2 s (2 s timeout plus about 2 s termination grace) |
| Same, **default** `init_timeout` (`None`) | Blocks **indefinitely** (cut only by my outer `asyncio.timeout`) | unbounded |

- `ClientGroup.from_config` on a config of good, bad and 401 servers failed the same way as the dict constructor. **(ran)**
- **Only the first error is raised** (`raise errors[0]`, `group.py:125`), and it is not tagged with the failing server's name. The error text does not always say which server failed (the 401 message does not). If the Settings UI needs "GitHub token rejected", probe members individually (below) so the failing key is known.
- `init_timeout=None` comes from `fastmcp.settings.client_init_timeout` (default `None`, `site-packages/fastmcp/settings.py:179-184`; applied at `client.py:501-504`). `timeout=None` (no per-request timeout) is the `Client.__init__` default (`client.py:433`). **Always pass `init_timeout=` and `timeout=` when constructing each `Client`.**
- `Client(MCPConfig)`, all servers bad: `RuntimeError: Client failed to connect: All MCP servers failed to connect` (**ran**), which wraps the `ConnectionError` raised at `config.py:280`.

**Partial-failure recipe for `ClientGroup` (ran, works):** connect each member individually in an `AsyncExitStack` with `try/except`. Then hand only the survivors to `MCPAdapter`. Because the adapter clones members that share the transport, and entering an already-connected client is a cheap refcount bump, it did not double-spawn:

```python
async with contextlib.AsyncExitStack() as stack:
    ok = {}
    for name, client in members.items():                 # members: dict[str, Client], keep_alive=False, init_timeout set
        try:
            await stack.enter_async_context(client)
            ok[name] = client
        except Exception as e:
            failures[name] = e                           # the name is known here -> per-Connection status for the UI
    if ok:
        async with MCPAdapter(ClientGroup(ok)) as adapter:
            tools = await adapter.list_tools()           # verified: good_* tools only, one spawn, clean exit
            ...
```

Connections here are sequential, so it costs the sum of handshake times rather than the max. If that matters, gather them with `asyncio.gather(..., return_exceptions=True)` (what `ClientGroup._connect_all` itself does, `group.py:110-125`).

### Other gotchas seen

- **Runtime failure of one member after connect.** `resolve_tool` only needs the **route's own** client to be connected, so a dead member does not break calls to healthy ones (`group.py:220-231`). A cold catalog load still needs all members (`group.py:194,223-227`). This is source-only; it was not tested by killing a member mid-run.
- **Tool-error vs transport-error** split: see the Q3 result-conversion notes.

---

## Extras relevant to building the per-run `mcp` subagent

1. **How subagents get tools, and why a static `SubAgent` is not enough.**
   - `SubAgent` has `tools: Sequence[BaseTool|Callable|dict]` (`deepagents/middleware/subagents.py:134`). Those are bound when `create_deep_agent` compiles the graph. `_build_task_tool` builds `subagent_graphs` once (`subagents.py:866`), and `atask` does `await subagent.ainvoke(subagent_state, subagent_config)` (`subagents.py:859`).
   - That matches the project's once-per-process zero-arg `graph()` factory (per `docs/research/deepagent-per-run-config-hooks.md`, sections 2a-2b, on branch `research/deepagent-per-run-config-hooks`).
   - For tools that exist only while a group is open, use `CompiledSubAgent = {"name", "description", "runnable"}` (`subagents.py:223-300`). `runnable` can be any `Runnable` whose state has `messages`.
   - **(ran)** A `RunnableLambda(async_fn)` runnable worked inside a real `create_deep_agent(model=..., subagents=[{"name": "mcp", "description": ..., "runnable": RunnableLambda(fn)}])` run. The function built the `ClientGroup`, opened `MCPAdapter`, called `create_agent(model, tools=tools)` and `ainvoke`d it. The inner agent called `local_ping` through the real stdio server, the orchestrator got `mcp done`, and no child process remained after the run. Models in that test were scripted fakes.
   - **Caveat:** this opens and closes the group **once per `task` delegation to `mcp`**, not once per agent run. If the orchestrator delegates to `mcp` N times in a run, that is N connect and teardown cycles (about 0.6 s each for a trivial Python server, unmeasured for docker). A literal per-run lifecycle needs group state kept outside LangGraph state (state must be serializable), for example a registry keyed by run or thread id with open in a run-start middleware hook and close in a run-end hook. That was **not** prototyped (see Open questions).
2. **Async-only and event loop.**
   - MCP tools are async-only (Q3). FastMCP's client creates its session with `asyncio.create_task`, so it needs an asyncio loop (`client.py:983`), which is what uvicorn and aegra use. Trio is not supported.
   - The repo's `tests/test_server_compat.py` uses `blockbuster`, configured to mirror `langgraph_runtime_inmem.queue._enable_blockbuster`, to forbid blocking calls on the loop. **(ran)** A full open, call and close of a stdio group passed under both the repo's relaxed ("server-like") config and blockbuster's strict config.
   - An HTTP group under the **strict** config failed once with `BlockingError: Blocking call to io.BufferedReader.read`. The trace shows a **first-use lazy import** (`httpcore2/_ssl.py` imports `truststore`, whose macOS module calls `platform.mac_ver()`, which reads a plist). It is a one-time, macOS-specific import cost, not per-call. **(ran)** The same HTTP scenario passed under the repo's relaxed config (which disables `io.BufferedReader.read`).
   - `aegra-api` 0.10.5 and `aegra-cli` 0.10.5 contain no `blockbuster` reference (grep), so the repo's blockbuster guard is a conservative test-time guard rather than something aegra enforces in production. If an `mcp` blockbuster test is added, pre-import `httpcore2._ssl` (or use the relaxed config) to avoid the false positive.
3. **Beta surface.**
   - `import langchain.mcp` emits `LangChainBetaWarning` (`langchain/mcp/__init__.py:26-30`). Silence it with `warnings.filterwarnings("ignore", category=LangChainBetaWarning)` if needed.
   - `requirements.txt` pins only `langchain[mcp]>=1.3`, with no upper bound. `fastmcp` and `mcp` are major versions (4.x and 2.x) that changed the HTTP stack (`httpx2`), so pin-drift is a real risk. Consider bounding `langchain` and `fastmcp` for this feature.
4. **Elicitation.** `MCPAdapter` arms every client so a server that asks for input mid-call is answered with a LangGraph `interrupt()` (`adapter.py:146-157`). This was not exercised. Whether the GitHub server elicits is unverified here.

---

## Surprises vs prior assumptions

| Prior assumption / ticket framing | What the installed version actually does |
|---|---|
| `ClientGroup` location and API may not exist in fastmcp 4 | It exists: `fastmcp.client.group.ClientGroup(Mapping[str, Client])` plus `from_config`. Docs page: https://gofastmcp.com/clients/client-groups. |
| There is a `langchain.mcp` `adapter.py` converting a `ClientGroup` | Confirmed: `MCPAdapter`, with `list_tools()` calling `as_langchain_tool(tool, client_or_group)`. There is no `MultiServerMCPClient` or `langchain-mcp-adapters` here. |
| Closing the `async with` ends the stdio subprocess | **False by default.** `keep_alive` defaults to True. Use `keep_alive=False` or call `close()` on each member; `ClientGroup` has no `close()`. |
| Tools must be used inside the block, or they break | They keep working after the block, but by reconnecting the whole group per call (respawn with `keep_alive=False`). Keep the tool use inside one `async with adapter:`. |
| One failing server shouldn't take the run down (ticket wording: "gotchas for aggregating") | `ClientGroup`: it does take the group down. `Client(MCPConfig)`: it doesn't. The recipe above gives partial tolerance on `ClientGroup`. |
| Sensible default timeouts | `init_timeout` and per-request `timeout` both default to `None`. A hung server hangs `connect` forever. |
| A rejected PAT would show up as an HTTP 401 | It surfaces as a generic `MCPError(-32603)` with no status (against a fake 401 server). |
| Child processes inherit the parent environment | Only `HOME, LOGNAME, PATH, SHELL, TERM, USER`, plus the explicit `env`. |
| Per-run group equals one open per agent run | With a `CompiledSubAgent` runnable it is one open per `task` delegation. |

## Open questions

1. **Literal per-run lifecycle.** Is one open per delegation acceptable? If not, the group needs a run-scoped owner (registry keyed by run or thread id, opened and closed from a run-start and run-end hook). This was not prototyped. It also needs a decision on crash and cancel cleanup: a per-run registry needs a guaranteed `close()` path, since `ClientGroup` has no `close()` and `keep_alive=False` only acts on graceful exits. Cancellation behaviour was read from source, not exercised.
2. **Which GitHub transport to build first.** Remote HTTP (`https://api.githubcopilot.com/mcp/` with `auth=pat`) avoids docker, subprocess lifecycle and the 2 s stdio shutdown grace. The stdio docker route matches the pinned OCI package, and neither was run against real GitHub. The registry marks neither credential as required on 1.12.2 (`docs/research/pin-mcp-registry-entries.md`, section 1). Startup time for the docker image and the real remote handshake were not measured.
3. **Partial-failure policy.** Fail the whole run, or degrade to the healthy Connections and tell the model/UI which one failed? The recipe above supports degrade, with the failing name known.
4. **Expired-token detection.** A 401 gives no status via the client. Decide whether to classify by `MCPError.code == -32603` plus the `mcp` subagent's first call, or do a direct `httpx` probe of the token. Observed against a local fake only, not real GitHub.
5. **Timeout values.** The right `init_timeout` and `timeout` for the GitHub docker image (image pull on first run?) are unmeasured.
6. **Microsoft 365 (deferred).** Its server is stdio via `npx` with Device Code. The mixed-transport mechanics here apply unchanged, and the token cache / login state it needs is not modelled by `env` alone. For a per-run process with `keep_alive=False`, the token cache must live on disk and be pointed at via `env` or `cwd`. Not verified.
7. **Pin the stack.** `langchain[mcp]>=1.3` is unbounded, and this whole surface is marked beta.
