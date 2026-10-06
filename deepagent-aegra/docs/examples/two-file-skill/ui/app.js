import { submit, onStatus } from "./skill-ui.js";

const $ = (id) => document.getElementById(id);
$("go").onclick = () =>
  submit({ period: $("period").value }, { ledger: $("ledger").files[0], bank: $("bank").files[0] });
onStatus(({ state, message }) => { $("status").textContent = message ?? state; });
