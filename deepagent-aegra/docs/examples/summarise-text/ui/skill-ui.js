export function submit(fields, files) {
  parent.postMessage({ type: "submit", fields, files }, "*");
}
export function onStatus(callback) {
  addEventListener("message", (e) => {
    if (e.source === parent && e.data?.type === "status") callback(e.data);
  });
}
