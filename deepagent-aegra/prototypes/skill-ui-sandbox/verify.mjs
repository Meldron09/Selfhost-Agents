// Real-browser check. Run: (uvicorn server:app --port 8001 &) (python -m http.server 8002 &) node verify.mjs
import { chromium } from "@playwright/test";
const b = await chromium.launch();
async function run(v) {
  const p = await b.newPage();
  const errs = [];
  p.on("console", (m) => m.type() === "error" && errs.push(m.text()));
  await p.goto(`http://localhost:8002/host.html?v=${v}`);
  const fr = p.frameLocator("#f");
  const out = { variant: v };
  try {
    await fr.locator("#title").waitFor({ timeout: 3000 });
    out.rendered = true;
    out.logoLoaded = await fr.locator("#logo").evaluate((i) => i.complete && i.naturalWidth > 0);
    await fr.locator("#file").setInputFiles({ name: "a.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
    await fr.locator("#go").click();
    await fr.locator("#status").filter({ hasText: "running" }).waitFor({ timeout: 3000 });
    out.statusRoundTrip = true;
    const r = await p.evaluate(async () => {
      const m = window.received[0];
      return { origin: m.origin, type: m.data.type, fields: m.data.fields, fileIsFile: m.data.files.a instanceof File, fileText: await m.data.files.a.text() };
    });
    Object.assign(out, r);
    out.probe = await fr.locator("#probe").textContent();
  } catch (e) { out.rendered = out.rendered ?? false; out.error = e.message.split("\n")[0]; }
  out.consoleErrors = errs.slice(0, 3);
  await p.close();
  return out;
}
for (const v of ["demo", "nocsp", "nocors"]) console.log(JSON.stringify(await run(v)));
await b.close();
