# Pin exact MCP Registry entries for GitHub, SharePoint, and Microsoft Teams

Research for issue #22 (child of the MCP support map, issue #19), resolving ADR-0009
(`deepagent-aegra/docs/adr/0009-mcp-config-sourced-from-pinned-registry-entries.md`).

**⚠️ Superseded 2026-09-28 — see "Amendment" section at the end.** Sections 2 and 3 below
(SharePoint, Microsoft Teams) record the *original* research and its pins, which the amendment
replaces with a single unified "Microsoft 365" connection. Kept here for the reasoning trail;
don't use the SharePoint/Teams pins below as the final answer.

All data pulled live from `https://registry.modelcontextprotocol.io/v0/servers?search=<term>`
on 2026-09-28. Full JSON responses are quoted verbatim below (trimmed to the relevant fields);
nothing here is invented.

## 1. GitHub — `io.github.github/github-mcp-server`

**Pin:** `io.github.github/github-mcp-server`, version **`1.12.2`** (current `isLatest: true` entry
as of 2026-09-28; the registry has published 65 versions of this name since 0.16.0).

Two connection shapes are declared on this entry:

### stdio package (self-hosted)

```json
{
  "registryType": "oci",
  "identifier": "ghcr.io/github/github-mcp-server:1.12.2",
  "transport": { "type": "stdio" },
  "runtimeArguments": [
    { "name": "-p", "value": "127.0.0.1:8085:8085", "type": "named",
      "description": "Publish the OAuth callback port to loopback so the in-container login callback is reachable" },
    { "name": "-e", "value": "GITHUB_OAUTH_CALLBACK_PORT=8085", "type": "named",
      "description": "Fixed OAuth callback port, matching the published port above" },
    { "name": "-e", "value": "GITHUB_PERSONAL_ACCESS_TOKEN={token}", "type": "named",
      "description": "Optional GitHub Personal Access Token. Omit to log in with OAuth on first use.",
      "variables": { "token": { "format": "string", "isSecret": true } } }
  ]
}
```

### remote (streamable-http)

```json
{
  "type": "streamable-http",
  "url": "https://api.githubcopilot.com/mcp/",
  "headers": [
    { "name": "Authorization", "isSecret": true,
      "description": "Authorization header with authentication token (PAT or App token)" }
  ]
}
```

**Credential schema:**

| Field | Shape | isRequired | isSecret | Notes |
|---|---|---|---|---|
| `GITHUB_PERSONAL_ACCESS_TOKEN` (stdio) | env var, `format: string` | **not declared required** on 1.12.2 | true | Server now supports interactive OAuth login on first use if the token is omitted (container publishes a loopback OAuth callback port). |
| `Authorization` (remote) | HTTP header | **not declared required** on 1.12.2 | true | PAT or GitHub App token. |

**Notable drift from the ticket's cited baseline:** the ticket's example (`ghcr.io/github/github-mcp-server:0.16.0`) is real and was confirmed — at that version `GITHUB_PERSONAL_ACCESS_TOKEN` was `isRequired: true`. Between 0.16.0 and the current 1.12.2 the project added an OAuth login path, so the token field is no longer marked required in the schema even though most self-hosted/headless deployments (like ours) will still need to supply it statically since there's no browser to complete an interactive OAuth prompt. **Recommendation for the auth window:** treat `GITHUB_PERSONAL_ACCESS_TOKEN` as effectively required for a headless deepagent-aegra deployment even though the registry schema itself doesn't mark it so on the latest version.

---

## 2. SharePoint

No SharePoint entry from the three prior desk-research candidates (`sekops-ch/sharepoint-mcp-server`,
`ftaricano/mcp-onedrive-sharepoint`, `eesb99/msgraph-mcp`) is published to the official registry —
none turned up under any search term tried (`sharepoint`, `sekops`, `ftaricano`, `eesb99`). Two other,
better candidates exist instead:

### Option A (recommended pin): `io.github.mindstone/mcp-server-microsoft-sharepoint`

**Pin:** version **`0.2.1`** (current `isLatest: true`, published 2026-08-10). Self-hostable npm
stdio package, part of an actively maintained monorepo of Microsoft 365 connectors
(`github.com/mindstone/mcp-servers`, subfolder `connectors/microsoft-sharepoint`). Description:
"Microsoft 365 SharePoint via Graph: sites, libraries, pages, lists, search, file/list mutations."

```json
{
  "registryType": "npm",
  "identifier": "@mindstone/mcp-server-microsoft-sharepoint",
  "version": "0.2.1",
  "transport": { "type": "stdio" },
  "environmentVariables": [
    { "name": "MS_CLIENT_ID", "isRequired": true, "format": "string",
      "description": "Microsoft Entra (Azure AD) application client ID" },
    { "name": "MS_CONFIG_DIR", "isRequired": true, "format": "filepath",
      "description": "Path to the per-user Microsoft config directory (credentials/, accounts.json)" },
    { "name": "MS_ACCOUNT_EMAIL", "format": "string",
      "description": "Account email when running in multi-account per-instance mode (falls back to the first account in accounts.json)" },
    { "name": "MS_MCP_PACKAGE_ID", "format": "string", "default": "Microsoft365SharePoint",
      "description": "Logical package ID surfaced in error responses" },
    { "name": "MICROSOFT_REQUEST_TIMEOUT_MS", "format": "number", "default": "60000",
      "description": "Override the upstream Microsoft Graph request timeout in milliseconds (max 300000 = 5 min)" }
  ]
}
```

**Gap vs. the ticket's assumption:** the ticket expected a clean client-ID + client-secret + tenant-ID
app-only schema. This entry doesn't declare that — it only requires `MS_CLIENT_ID` plus
`MS_CONFIG_DIR`, a filesystem path to a pre-populated credentials directory (`credentials/`,
`accounts.json`). No client-secret or tenant-ID field is declared at all. That strongly implies an
out-of-band interactive login step (device code or browser) populates `MS_CONFIG_DIR` before the
MCP server can use it — the registry schema doesn't capture that flow, only the resulting file-path
dependency. This is a real limitation for a headless Connection Store: there's no purely
declarative secret set the auth window can collect and hand to the server; a first-run interactive
login against that config directory is implied.

### Option B (not recommended as primary pin, but worth recording): `com.microsoft/workiq-sharepointliststools`

Published under the **`com.microsoft`** namespace (reverse-DNS-verified, i.e. an entry actually
controlled by Microsoft), from `github.com/bap-microsoft/MCP-Platform` — Microsoft's Business
Applications Platform org. Version `1.0.0`, `isLatest: true`.

```json
{
  "name": "com.microsoft/workiq-sharepointliststools",
  "title": "SharePointListsTools",
  "description": "MCP server providing Microsoft Graph SharePoint tools for Lists.",
  "remotes": [
    {
      "type": "streamable-http",
      "url": "https://agent365.svc.cloud.microsoft/agents/tenants/{tenant_id}/servers/mcp_SharePointListsTools",
      "variables": {
        "tenant_id": { "description": "Microsoft Entra tenant ID", "isRequired": true }
      }
    }
  ]
}
```

This is the most *trustworthy publisher* of anything found (genuinely Microsoft-owned), but it's not
a fit for our architecture: it's a remote gateway into Microsoft's own hosted **Agent 365 / Work IQ**
platform, not a self-hostable server we spin up with injected credentials. Its only declared field is
`tenant_id` (not secret) — the real authentication (presumably OAuth against Entra, tied to Agent 365
tenant enrollment) isn't captured as static fields at all, and Agent 365 enrollment is itself a
separate Microsoft 365 product prerequisite our stack doesn't assume. Recorded for completeness /
future reference, not pinned.

**Recommendation:** pin **Option A** (`io.github.mindstone/mcp-server-microsoft-sharepoint@0.2.1`)
as the closest real, self-hostable, actively-maintained entry, while flagging the `MS_CONFIG_DIR`
interactive-login gap explicitly in the auth window design.

---

## 3. Microsoft Teams

`floriscornel/teams-mcp` (github.com/floriscornel/teams-mcp) is **not published to the official
registry** — confirmed via direct search (`floriscornel`: 0 results).

Three real, published candidates were found; none has a usable static credential schema for a
device-code-free headless deployment:

| Registry name | Version | Transport | Declared credentials | Verdict |
|---|---|---|---|---|
| `com.microsoft/workiq-teamsserver` | 1.0.0 (latest) | remote streamable-http | `tenant_id` only (not secret), URL path variable into `agent365.svc.cloud.microsoft` | Official Microsoft namespace, but same Agent 365-gateway limitation as the SharePoint option — not self-hostable, no static creds, real auth (OAuth) not captured in schema. |
| `io.github.chrischall/microsoft-teams-mcp` | 0.2.3 (latest) | npm stdio | Only `TEAMS_WS_PORT` (a local WebSocket bridge port, not a credential) | Works by proxying your logged-in **browser** session ("via your browser" in its description) — no Graph/API credential at all. This publisher (`chrischall`) has also published dozens of unrelated wrapper packages (ticketing, hiking trails, real-estate portals...) under the same GitHub account — exactly the low-signal noise ADR-0009 warns live search surfaces. |
| `io.github.SurgeEnterpriseAI/teams-mcp-server` | 1.0.0 (latest) | npm stdio | **None declared** — the package entry has no `environmentVariables` field at all | No schema to render an auth window from. |

```json
// com.microsoft/workiq-teamsserver, full entry
{
  "name": "com.microsoft/workiq-teamsserver",
  "title": "Work IQ Teams MCP Server",
  "description": "Manage Microsoft Teams chats, channels, users, and messages via Graph API.",
  "version": "1.0.0",
  "remotes": [
    {
      "type": "streamable-http",
      "url": "https://agent365.svc.cloud.microsoft/agents/tenants/{tenant_id}/servers/mcp_TeamsServer",
      "variables": {
        "tenant_id": { "description": "Microsoft Entra tenant ID", "isRequired": true }
      }
    }
  ]
}
```

**Gap called out explicitly (this is the important finding for the map):** none of the three
published Teams entries documents a Device Code / interactive-auth requirement as a schema field —
registry `server.json` only models static secret fields (env vars / headers / URL variables), not
OAuth flow types, so a device-code requirement (as `floriscornel/teams-mcp`'s own README reportedly
describes, per prior desk research) can never show up here even if that project were published. Of
the three real entries: the official one (`workiq-teamsserver`) sidesteps the problem by being a
hosted Microsoft gateway requiring Agent 365 enrollment (a different product dependency, not a
credential we collect); the two community ones either have no real credential (browser-session proxy)
or no declared schema at all.

**Recommendation:** pin `com.microsoft/workiq-teamsserver@1.0.0` as the *closest real, officially
published* entry, but flag in the auth-window design that: (a) it requires the tenant to be enrolled
in Microsoft's Agent 365 / Work IQ platform (a separate Microsoft 365 prerequisite, not just an Entra
app registration), and (b) its actual auth handshake is not captured by the registry schema at all —
only `tenant_id` is declared, no secret field exists to collect. If self-hosted device-code Teams
access (matching the original `floriscornel/teams-mcp` design intent) is still wanted, that server
would need to be published to the registry itself, or handled outside the pinned-registry-entry
pattern entirely (ADR-0009 gap: the pattern assumes a static field schema; device-code/interactive
flows don't have one).

---

## Summary table

| Server | Registry name | Version pinned | Transport | Credential shape | Fit |
|---|---|---|---|---|---|
| GitHub | `io.github.github/github-mcp-server` | `1.12.2` | stdio (oci) + remote (streamable-http) | `GITHUB_PERSONAL_ACCESS_TOKEN` env var / `Authorization` header, both `isSecret: true`, not marked required on latest (was required on 0.16.0) | Clean fit |
| SharePoint | `io.github.mindstone/mcp-server-microsoft-sharepoint` | `0.2.1` | stdio (npm) | `MS_CLIENT_ID` (required) + `MS_CONFIG_DIR` (required, filepath to a pre-populated credential store) | Partial fit — implies an out-of-band login step, no static secret alone suffices |
| Microsoft Teams | `com.microsoft/workiq-teamsserver` | `1.0.0` | remote (streamable-http) | `tenant_id` only (not secret); real auth handshake undeclared | Weak fit — official publisher, wrong architecture (hosted Agent 365 gateway); no self-hostable, credentialed alternative exists in the registry today |

---

## Amendment (2026-09-28): unify SharePoint + Teams as one "Microsoft 365" connection

Directed by the user: use **[Softeria/ms-365-mcp-server](https://github.com/Softeria/ms-365-mcp-server)**
instead of separate SharePoint and Teams pins.

**Pin:** `io.github.Softeria/ms-365-mcp-server`, version **`0.156.2`** (`isLatest: true`, published
2026-09-27T18:27:40Z — confirmed live against `registry.modelcontextprotocol.io/v0/servers?search=ms-365-mcp-server`).

```json
{
  "name": "io.github.Softeria/ms-365-mcp-server",
  "title": "Microsoft 365 MCP Server",
  "version": "0.156.2",
  "packages": [
    { "registryType": "npm", "identifier": "@softeria/ms-365-mcp-server", "transport": { "type": "stdio" } }
  ]
}
```

The registry package entry itself declares no `environmentVariables` (bare npm id + stdio transport
only) — the real credential/config shape lives in the project's README, not registry metadata:

| Env var | Required | Secret | Notes |
|---|---|---|---|
| `MS365_MCP_CLIENT_ID` | no | no | Optional — defaults to a built-in Softeria Azure AD app registration |
| `MS365_MCP_TENANT_ID` | no | no | Default `common`; personal Microsoft accounts must use `consumers` (refresh against `common` broke for personal accounts as of June 2026) |
| `MS365_MCP_CLIENT_SECRET` | no | yes | Only needed for a confidential-client (your own) app registration |
| `MS365_MCP_OAUTH_TOKEN` | no | yes | BYOT mode — supply a pre-obtained token instead of doing the login flow in-process |
| `MS365_MCP_ALLOWED_SCOPES` / `MS365_MCP_EXTRA_SCOPES` | no | no | Narrow/extend the Graph scopes requested at login |

**Why this replaces both prior pins:**
- **One connection, one login** — `--org-mode` unlocks SharePoint sites/lists/drives *and* Teams
  chats/channels under the same MSAL session and token cache. No separate credential shape per
  service (resolves the SharePoint `MS_CONFIG_DIR` gap: this project's device-code login populates
  its own token cache internally, nothing pre-populated by hand).
- **No app-only mode exists in this project at all** — auth is delegated OAuth exclusively (device
  code by default via `--login`; browser/HTTP-OAuth mode; or BYOT). This removes the
  split-decision problem entirely: Teams' Graph permissions were already forced into delegated-only
  (Microsoft gates app-only chat/message access hard), and now SharePoint just uses the same
  delegated login rather than needing its own separate story.
- **Self-hostable and actively maintained**: 998★, MIT, near-daily releases (latest yesterday),
  local process (`npx`/npm install) or `ghcr.io/softeria/ms-365-mcp-server` Docker image — not a
  hosted gateway like the `com.microsoft/workiq-*` Agent 365 entries, which remain non-self-hostable
  dead ends (confirmed unchanged).
- The one alternative candidate found (`ai.smithery/DynamicEndpoints-m365-core-mcp`) has a genuinely
  404'd source repo as of this research — not verifiably alive, so not considered.

**Scope decision:** `--org-mode` also unlocks mail/calendar/presence/shared-mailboxes/user-management
tools beyond SharePoint+Teams. Per the map, this connection is filtered down via
`--enabled-tools 'sharepoint|site|drive|teams|chat'` (or equivalent) rather than exposing the full
org-mode surface — narrower blast radius than what was asked for, widen later if wanted.

**Net result — curated list is now two MCPs, not three:**

| Server | Registry name | Version | Auth Mode | Credential shape |
|---|---|---|---|---|
| GitHub | `io.github.github/github-mcp-server` | `1.12.2` | Credential Form | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| Microsoft 365 (SharePoint + Teams) | `io.github.Softeria/ms-365-mcp-server` | `0.156.2` | Device Code | `MS365_MCP_TENANT_ID` (+ optional client ID/secret) |
