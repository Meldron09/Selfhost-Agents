# Writing a Skill UI

A Skill is a folder you zip and upload on the Skills page:

```
my-skill/
  SKILL.md        required: frontmatter (name, description) + the instructions
  rules.md        optional reference files (.pdf .xlsx .xls .docx .pptx .txt .md), read by the agent on demand
  ui/
    index.html    optional: your screen; without a `ui/` folder the app shows a plain text box and file picker
```

`name` is lowercase letters, digits and single hyphens, up to 64 characters (`two-file-reconcile`), and both `name` and `description` are required. The zip is refused, with the rule it broke, if `SKILL.md` or its frontmatter is missing, `ui/` has no `index.html`, it is over 25 MB, a path escapes the folder or is a symlink, or a file outside `ui/` is not a reference type (a stray `LICENSE` or `.png` fails the install). A complete working folder is in [`examples/two-file-skill/`](examples/two-file-skill).

`ui/` is a static web bundle: `ui/index.html` is the entry and everything else loads from it with **relative paths** (`./skill-ui.js`, not `/skill-ui.js`). Write plain HTML and ES modules by hand, or build with any framework and zip the build output as `ui/` (Vite: set `base: './'`; Create React App: `"homepage": "."`).

The app shows your UI in `<iframe sandbox="allow-scripts">`. That means (ADR-0010):

- no cookies, `localStorage`, or access to the app's page;
- no network: `fetch`, XHR, WebSockets and form posts are blocked;
- scripts, styles and images only from files in your own bundle (plus `data:` images): **inline `<script>` and `<style>` blocks and `style=""` attributes are blocked**, so keep them in files (`<script type="module" src="./app.js">`). Some builds inline a runtime chunk by default (Create React App: set `INLINE_RUNTIME_CHUNK=false`);
- no `alert`/`confirm`, popups or downloads: show messages in your own page.

The contract has three parts: you send `submit`, the app sends `status`, and the app renders the result. You never render it yourself: a standard result panel (final message, downloadable Outputs, any approval) appears below your UI.

## UI to app: `submit`

```js
parent.postMessage({
  type: "submit",
  fields: { period: "Q1", strict: true },          // any JSON-able values
  files: { ledger: ledgerFile, bank: bankFile },   // name -> File (or an array of Files)
}, "*");
```

The app uploads the files, rejects types the agent cannot read (`.pdf .xlsx .xls .docx .pptx .txt .md`), and starts the Skill Run. Only the first `submit` starts a Run.

In `SKILL.md`, refer to the inputs by the names you chose: the agent receives `fields` as a JSON block, verbatim, and each uploaded file listed under its field name (`ledger`, `bank`).

## App to UI: `status`

```js
{ type: "status", state: "running" }                         // "queued" | "running" | "done" | "failed"
{ type: "status", state: "failed", message: "Unsupported file type(s): photo.png. …" }
```

The app sends nothing until a Run starts (nothing at load), and `queued` is part of the contract for when Runs queue but is not sent yet. `message` appears when the app refused the submission (for example an unsupported file) so you can show it and let the person submit again; a failure later in the Run arrives as `failed` with no `message`. Empty file slots are dropped, and a malformed `submit` (for example a non-File where a file belongs) is ignored with no reply. Listen only if you want to show progress.

## Copy-paste helper

Save as `ui/skill-ui.js` and `import { submit, onStatus } from "./skill-ui.js"` (or paste into your own code):

```js
export function submit(fields, files) {
  parent.postMessage({ type: "submit", fields, files }, "*");
}
export function onStatus(callback) {
  addEventListener("message", (e) => {
    if (e.source === parent && e.data?.type === "status") callback(e.data);
  });
}
```

Messages from your frame arrive at the app with origin `"null"`; the app identifies you by the frame itself, and you should do the same (`e.source === parent`), not by origin.
