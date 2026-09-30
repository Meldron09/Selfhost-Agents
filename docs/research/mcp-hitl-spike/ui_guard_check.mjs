// Q4 check: does agent-chat-ui's own guard accept the payload? Build the guard first, from agent-chat-ui/:
//   ./node_modules/.bin/esbuild src/lib/agent-inbox-interrupt.ts --bundle --format=esm --platform=node \
//       --alias:@=./src --outfile=<here>/guard.mjs
// then `node ui_guard_check.mjs` (adjust the SDK path below to your checkout).
import { isAgentInboxInterruptSchema } from "./guard.mjs";
import { normalizeInterruptForClient } from "/Users/meldron/Documents/Meldron/Selfhost-Agents/agent-chat-ui/node_modules/@langchain/langgraph-sdk/dist/ui/interrupts.js";

// Verbatim from the aegra stream_graph_events output (values event, root ns) in test_q4.
const raw = {
  value: {
    action_requests: [
      { name: "gh_write_thing", args: { name: "x" }, description: "Tool execution requires approval\n\nTool: gh_write_thing\nArgs: {'name': 'x'}" },
    ],
    review_configs: [{ action_name: "gh_write_thing", allowed_decisions: ["approve", "reject"] }],
  },
  id: "9bbc12106bacae29dea378c2fcc7dd20",
};
const normalized = normalizeInterruptForClient(raw); // what useStream().interrupt holds
console.log("guard(raw)       =", isAgentInboxInterruptSchema(raw));
console.log("guard(normalized)=", isAgentInboxInterruptSchema(normalized));
console.log("guard([normalized])=", isAgentInboxInterruptSchema([normalized]));
console.log("guard(unannotated-style, no review_configs)=", isAgentInboxInterruptSchema({ value: { action_requests: raw.value.action_requests } }));
