import { useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import logo from "./logo.svg";

// The ~30-line helper authors would copy-paste (ADR-0010 contract).
const host = {
  submit: (fields, files) => parent.postMessage({ type: "submit", fields, files }, "*"),
  onStatus: (cb) => addEventListener("message", (e) => e.data?.type === "status" && cb(e.data)),
};

function App() {
  const [status, setStatus] = useState("idle");
  const [file, setFile] = useState(null);
  useEffect(() => host.onStatus((m) => setStatus(m.state)), []);
  return (
    <div>
      <img id="logo" src={logo} alt="asset" />
      <h1 id="title">React Skill UI</h1>
      <input id="file" type="file" onChange={(e) => setFile(e.target.files[0])} />
      <button id="go" onClick={() => host.submit({ note: "hi" }, file ? { a: file } : {})}>Submit</button>
      <p id="status">status: {status}</p>
      <p id="probe"></p>
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);

// Probe the sandbox limits the ADR claims (no storage, no API reach).
const probe = [];
try { localStorage.getItem("x"); probe.push("localStorage:ALLOWED"); } catch { probe.push("localStorage:blocked"); }
try { probe.push("cookie:" + (document.cookie === "" ? "empty" : "readable")); } catch { probe.push("cookie:blocked"); }
fetch("http://localhost:8000/health").then(() => probe.push("fetch-api:ALLOWED"), () => probe.push("fetch-api:blocked"))
  .finally(() => setTimeout(() => (document.getElementById("probe").textContent = probe.join(" | ")), 0));
