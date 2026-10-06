# Selfhost-Agents

A chat app backed by a team of AI agents that you run entirely on your own machine.

Drop in a PDF, spreadsheet, Word doc, or slide deck and ask questions about it. Ask for a report and get a real `.xlsx`, `.docx`, `.pptx`, or `.txt` file back to download. Turn on web search when a question needs fresh information, or connect your GitHub account or n8n instance so the agent can look things up and run workflows there. The AI model runs locally through [Ollama](https://ollama.com), so your files never have to leave your computer.

## What it can do

| Ask it to… | What happens |
| --- | --- |
| **Read a file you attach** | A `file-reader` agent opens PDF, Excel, Word, PowerPoint, or text files and answers from their real content. |
| **Make a file for you** | An `output-writer` agent builds an Excel, Word, PowerPoint, or text file and hands you a download link. |
| **Research something online** | A `web-search` agent searches the web and reads pages. It's off by default, and you switch it on per message with the **Web Search** toggle. |
| **Work with GitHub or n8n** | An `mcp` agent uses your connected GitHub account and n8n instance (set up once in **Settings**), so it can search repositories or run your n8n workflows. Anything that could change data asks for your approval first. |
| **Keep reusable Skills** | Open **Skills** in the chat header to install a Skill by uploading it as a zip, see every installed Skill with its name and description, replace one by uploading a new zip (kept as it was if the new zip is refused), or delete one after a confirmation. A bad zip is refused with the rule it broke. **Open** a Skill to run it: a built-in screen takes a free-text box, files (unsupported types are rejected with a message) and the web search switch (off by default), then shows the Run's status, final message and downloadable Outputs. A Skill that ships a `ui/` folder gets its own screen instead (for example two labelled upload slots), shown in a locked-down sandbox that cannot reach the app's API or other data; the app uploads the files and shows the same status and result panel (author guide: [`deepagent-aegra/docs/skill-ui-contract.md`](deepagent-aegra/docs/skill-ui-contract.md)). A Run is hidden from the chat list, so a normal chat keeps working alongside it. Only one Skill Run is active at a time: one submitted meanwhile shows as **queued** (in the result panel and to the Skill's screen) and starts automatically, in submission order, when the earlier Run is done or has failed. The result panel has a **Cancel** button while a Run is queued or running: cancelling a queued Run just removes it, cancelling a running Run stops it and lets the next queued Run start; the panel (and the Skill's screen) then show **cancelled**. (Approvals and run history come in later changes.) |

A main **orchestrator** agent reads your request and hands each part to the right specialist. There is no code-execution sandbox: the agents can only use the tools listed above.

## How it fits together

```
 Browser                      Your machine
┌───────────────┐      ┌──────────────────────────────────────────┐
│ agent-chat-ui │ ───► │ deepagent-aegra  (agents + file storage) │ ──► Ollama (local model)
│ (Next.js chat)│ ◄─── │ served by aegra, state kept in Postgres  │ ──► Tavily (web search, optional)
└───────────────┘      └──────────────────────────────────────────┘ ──► GitHub / n8n MCP (optional)
```

| Folder | What it is |
| --- | --- |
| [`deepagent-aegra/`](deepagent-aegra) | The backend. It holds the orchestrator and its four subagents, the file upload/download API, and the Docker setup. Built on [deepagents](https://github.com/langchain-ai/deepagents) and served with [aegra](https://github.com/ibbybuilds/aegra). |
| [`agent-chat-ui/`](agent-chat-ui) | The chat interface (a fork of [langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui), added here as a git submodule). It adds file attachments, download links, the Web Search toggle, and a Settings dialog for connections. |
| [`docs/`](docs) | Research notes and the project's working docs. |

## Quick start

**You need:** [Docker](https://www.docker.com/), [Ollama](https://ollama.com) running on your machine, and [pnpm](https://pnpm.io) with Node.js for the chat UI.

**1. Get the code.** The chat UI is a submodule, so clone with `--recurse-submodules`:

```bash
git clone --recurse-submodules https://github.com/Meldron09/Selfhost-Agents.git
cd Selfhost-Agents
```

**2. Pull a model.** It must support tool calling (`ollama show <model>` lists `tools`):

```bash
ollama pull gpt-oss:20b
```

**3. Start the backend.**

```bash
cd deepagent-aegra
cp .env.example .env
# edit .env and set OLLAMA_MODEL (e.g. gpt-oss:20b) and OLLAMA_CONTEXT_WINDOW (e.g. 32768)
docker compose up --build
```

This starts Postgres and the agent server on `http://localhost:8000`.

**4. Start the chat UI** in a second terminal:

```bash
cd agent-chat-ui
pnpm install
NEXT_PUBLIC_API_URL=http://localhost:8000 NEXT_PUBLIC_ASSISTANT_ID=agent pnpm dev
```

Open <http://localhost:3000> and start chatting.

### Optional extras

Set these in `deepagent-aegra/.env`, then restart the backend.

- **Web search:** set `TAVILY_API_KEY` (get one at [tavily.com](https://tavily.com)), then flip the **Web Search** toggle in the chat box.
- **GitHub and n8n connections:** set `MCP_STORE_KEY` to a fresh encryption key. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Then open **Settings → MCP** in the UI and paste a GitHub personal access token. To connect n8n, enable instance-level MCP in n8n (**Settings → Instance-level MCP**), then in the same tab enter your instance's MCP URL and its access token. Losing the key makes saved credentials unreadable.

## Learn more

- [`deepagent-aegra/README.md`](deepagent-aegra/README.md): commands for testing, running, and checking the backend, plus a map of the code.
- [`deepagent-aegra/docs/skill-ui-contract.md`](deepagent-aegra/docs/skill-ui-contract.md): the one-page guide for writing a Skill UI (folder layout, the `submit`/`status` messages, sandbox limits, a copy-paste helper).
- [`deepagent-aegra/CONTEXT.md`](deepagent-aegra/CONTEXT.md): the project's vocabulary (Attachment, Output, Connection, and so on).
- [`deepagent-aegra/docs/adr/`](deepagent-aegra/docs/adr): why each major design decision was made.
- [`docs/research/`](docs/research): research and spikes behind those decisions.
- [Issues](https://github.com/Meldron09/Selfhost-Agents/issues): the ticket history, from the original spec (#13) through MCP support (#19).
