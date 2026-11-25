// Talking to the local musicheck server.
const TOKEN = document.querySelector('meta[name="musicheck-token"]').content;
export const DESKTOP = document.querySelector('meta[name="musicheck-desktop"]').content === "1";

export class ApiError extends Error {}

async function call(method, path, body) {
  const init = { method, headers: { "X-Musicheck-Token": TOKEN } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("musicheck stopped responding. Restart the app.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error || `request failed (${res.status})`);
  return data;
}

export const api = {
  state: () => call("GET", "/api/state"),
  tracks: () => call("GET", "/api/tracks"),
  spectra: () => call("GET", "/api/spectra"),
  track: (id) => call("GET", `/api/track/${id}`),
  scan: () => call("POST", "/api/scan", {}),
  open: (path) => call("POST", "/api/open", { paths: [path] }),
  pickFolder: () => call("POST", "/api/pick-folder", {}),
  reveal: (id) => call("POST", "/api/reveal", { id }),
  plan: (req) => call("POST", "/api/organize/plan", req),
  apply: (req) => call("POST", "/api/organize/apply", { ...req, confirm: true }),
  audioUrl: (id) => `/api/track/${id}/audio?t=${encodeURIComponent(TOKEN)}`,
  coverUrl: (id) => `/api/track/${id}/cover?t=${encodeURIComponent(TOKEN)}`,
};
