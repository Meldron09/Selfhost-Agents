# Writing a Skill UI

A Skill's `ui/` folder is a static web bundle: `ui/index.html` is the entry, and everything else is loaded from it with **relative paths**. Write it by hand, or build it with any tool and ship the output (Vite: set `base: './'`). Install the Skill by zipping the folder (`SKILL.md`, reference files, `ui/`) and uploading it on the Skills page.

The app shows your UI in `<iframe sandbox="allow-scripts">`. That means (ADR-0010):

- no cookies, `localStorage`, or access to the app's page;
- no network: `fetch`, XHR, WebSockets and form posts are blocked;
- images and scripts only from your own bundle (or `data:` images).

Everything goes through three messages. You never render the result: the app shows a standard result panel (final message, downloadable Outputs, any approval) below your UI.

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

`message` appears when the app refused the submission (for example an unsupported file) so you can show it and let the person submit again. Listen only if you want to show progress.

## Copy-paste helper

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
