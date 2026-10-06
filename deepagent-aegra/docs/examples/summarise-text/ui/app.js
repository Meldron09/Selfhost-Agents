import { submit, onStatus } from "./skill-ui.js";

const $ = (id) => document.getElementById(id);
$("go").onclick = () => submit({ style: $("style").value }, { document: $("document").files[0] });
onStatus(({ state, message }) => { $("status").textContent = message ?? state; });
