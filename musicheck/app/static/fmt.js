// Formatting and wording shared by the list and the detail.

export const SEVERITY = { OK: 0, SUSPECT: 1, FAKE: 2, ERROR: 3 };
export const NAV_ORDER = ["FAKE", "SUSPECT", "OK", "ERROR"];
export const VERDICT_WORD = { OK: "OK", SUSPECT: "Suspect", FAKE: "Fake", ERROR: "Error" };
export const CODE = { OK: "#f2f2f0", SUSPECT: "#f2b81c", FAKE: "#e5484d", ERROR: "#5b86ff" };

// Quality classes, worst to best, as named by musicheck's "real" field.
export const CLASSES = ["<=64k", "~96k", "~128k", "~160k", "~192k", "~256k", "~320k", "full band"];
const CLASS_KBPS = [64, 96, 128, 160, 192, 256, 320];

const MINUS = "−";

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export const blank = (v) => v === null || v === undefined || v === "";

export function khz(v, digits = 1) {
  return blank(v) ? "" : `${Number(v).toFixed(digits)} kHz`;
}

export function db(v, digits = 1) {
  if (blank(v)) return "";
  const n = Number(v);
  return `${n < 0 ? MINUS : ""}${Math.abs(n).toFixed(digits)} dB`;
}

export function signed(v, digits = 0) {
  const n = Number(v);
  return `${n < 0 ? MINUS : ""}${Math.abs(n).toFixed(digits)}`;
}

export function duration(s) {
  if (blank(s)) return "";
  const t = Math.round(Number(s));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function rate(hz) {
  return blank(hz) ? "" : `${(Number(hz) / 1000).toLocaleString("en", { maximumFractionDigits: 2 })} kHz`;
}

export function mb(v) {
  return blank(v) ? "" : `${Number(v).toLocaleString("en", { maximumFractionDigits: 1 })} MB`;
}

export const titleOf = (t) => t.title || t.file.replace(/\.[^.]+$/, "");

export function catno(n, total) {
  return String(n).padStart(total > 999 ? 4 : 3, "0");
}

// Index of the class the file claims, -1 when unknown.
export function claimedClass(t) {
  if (t.lossless) return CLASSES.length - 1;
  if (!t.bitrate) return -1;
  let idx = 0;
  CLASS_KBPS.forEach((k, i) => { if (t.bitrate >= k * 0.97) idx = i; });
  return idx;
}

export const realClass = (t) => CLASSES.indexOf(t.real);

export function realLabel(t) {
  return t.real === "?" || blank(t.real) ? "not judged" : t.real;
}

// Eight cells: the real class filled, the claimed class outlined.
export function qcode(t) {
  const real = realClass(t), claimed = claimedClass(t);
  const tone = t.verdict === "FAKE" ? " short" : t.verdict === "SUSPECT" ? " near" : "";
  const cells = CLASSES.map((_, i) => {
    const cls = [i <= real ? "r" : "", i === claimed ? "c" : ""].join(" ").trim();
    return `<i${cls ? ` class="${cls}"` : ""}></i>`;
  }).join("");
  const label = real < 0 ? "real quality not judged"
    : `real quality ${t.real}${claimed >= 0 ? `, claims ${CLASSES[claimed]}` : ""}`;
  return `<span class="qcode${tone}" role="img" aria-label="${esc(label)}">${cells}</span>`;
}

export function verdictTag(v) {
  return `<span class="verdict v-${esc(v)}"><i class="code" aria-hidden="true"></i>${esc((v || "").toUpperCase())}</span>`;
}

function cap(s) {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

// The one sentence that says why a track got its verdict.
export function sentence(t, issues) {
  const main = (issues || []).find((i) => i.level === t.verdict && t.verdict !== "OK");
  if (main) {
    const lead = { FAKE: "Fake.", SUSPECT: "Suspect.", ERROR: "Unreadable." }[t.verdict];
    return { lead, text: `${cap(main.message)}.` };
  }
  if ((issues || []).some((i) => i.code === "TOO_QUIET")) {
    return { lead: "Not judged.", text: "Too short or too quiet to read its spectrum." };
  }
  const claim = t.lossless ? `a ${t.format} file` : t.claimed;
  if (!blank(t.cutoff_khz)) {
    const exp = blank(t.expected_khz) ? "" : `, at or above the ${t.expected_khz} kHz a genuine one reaches`;
    return { lead: "OK.", text: `Sound reaches ${t.cutoff_khz} kHz, as expected of ${claim}${exp}.` };
  }
  return { lead: "OK.", text: `Full band: sound reaches ${khz(t.band_khz)}, the top of the format.` };
}
