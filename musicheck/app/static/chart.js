// The selected track's averaged spectrum, with its cutoff and the reference.
import { CODE, signed } from "./fmt.js";

const NS = "http://www.w3.org/2000/svg";
const PAD = { l: 34, r: 12, t: 22, b: 22 };

function refLevel(dbs, nyq) {
  // Loudness of the 0.2–8 kHz band: the curve is drawn relative to it.
  let best = -Infinity;
  dbs.forEach((v, i) => {
    const f = ((i + 0.5) / dbs.length) * nyq;
    if (f > 200 && f < 8000 && v > best) best = v;
  });
  return Number.isFinite(best) ? best : Math.max(...dbs);
}

export function renderChart(svg, readout, detail) {
  const spec = detail.spectrum;
  const t = detail.track;
  const box = svg.getBoundingClientRect();
  const W = Math.max(200, Math.round(box.width));
  const H = Math.max(120, Math.round(box.height));
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.textContent = "";
  const dbs = spec.db || [];
  const nyq = (spec.nyquist_hz || 22050) / 1000;
  if (!dbs.length) {
    svg.innerHTML = `<text x="${PAD.l}" y="${H / 2}" class="chart-label">NO SPECTRUM: THE FILE COULD NOT BE DECODED</text>`;
    return;
  }
  const top = refLevel(dbs, nyq * 1000) + 10;
  const span = 110;
  const x = (k) => PAD.l + (k / nyq) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + Math.min(1, Math.max(0, (top - v) / span)) * (H - PAD.t - PAD.b);
  const parts = [];

  // grid
  const stepK = nyq > 30 ? 8 : nyq > 15 ? 4 : 2;
  for (let k = 0; k <= nyq + 1e-6; k += stepK / 2) {
    const major = Math.abs(k / stepK - Math.round(k / stepK)) < 1e-6;
    parts.push(`<line class="chart-grid" x1="${x(k).toFixed(1)}" x2="${x(k).toFixed(1)}" y1="${PAD.t}" y2="${H - PAD.b}" ${major ? "" : 'opacity="0.5"'}/>`);
    if (major) parts.push(`<text class="chart-label" x="${x(k).toFixed(1)}" y="${H - 6}" text-anchor="middle">${k}</text>`);
  }
  const tall = H - PAD.t - PAD.b >= 120;
  for (let d = 0; d <= span; d += tall ? 20 : 50) {
    const yy = y(top - 10 - d);
    if (yy > H - PAD.b + 0.5) break;
    parts.push(`<line class="chart-grid" x1="${PAD.l}" x2="${W - PAD.r}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" opacity="0.5"/>`);
    parts.push(`<text class="chart-label" x="${PAD.l - 6}" y="${(yy + 3).toFixed(1)}" text-anchor="end">${d ? signed(-d) : "0"}</text>`);
  }

  // the gap between where the sound stops and where it should reach
  const cut = t.cutoff_khz;
  const ref = spec.reference_khz;
  const tone = CODE[t.verdict] || CODE.OK;
  if (cut && ref && ref > cut && (t.verdict === "FAKE" || t.verdict === "SUSPECT")) {
    parts.push(`<defs><pattern id="gap" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="${tone}" stroke-width="1.5" opacity="0.45"/></pattern></defs>`);
    parts.push(`<rect x="${x(cut).toFixed(1)}" y="${PAD.t}" width="${(x(ref) - x(cut)).toFixed(1)}" height="${H - PAD.t - PAD.b}" fill="url(#gap)"/>`);
  }

  // curve
  const pts = dbs.map((v, i) => `${x(((i + 0.5) / dbs.length) * nyq).toFixed(1)},${y(v).toFixed(1)}`);
  parts.push(`<polyline class="chart-line" points="${pts.join(" ")}"/>`);

  // markers
  if (ref) {
    const label = { lossless: "LOSSLESS", "hi-res": "HI-RES" }[spec.reference_label] || "EXPECTED";
    parts.push(`<line class="chart-exp" x1="${x(ref).toFixed(1)}" x2="${x(ref).toFixed(1)}" y1="${PAD.t}" y2="${H - PAD.b}"/>`);
    const text = `${label} ${ref.toFixed(1)}`;
    const fits = x(ref) + 4 + text.length * 8 < W - PAD.r;
    const after = fits && !(cut && cut > ref && x(cut) - x(ref) < 130);
    // No room on the right: hang the label inside the plot, under the cutoff label.
    const ly = after ? PAD.t - 8 : PAD.t + 14;
    parts.push(`<text class="chart-mark" fill="#b6b6b1" x="${(x(ref) + (after ? 4 : -4)).toFixed(1)}" y="${ly}" text-anchor="${after ? "start" : "end"}">${text}</text>`);
  }
  if (cut) {
    const near = ref && Math.abs(x(ref) - x(cut)) < 130;
    const overflow = x(cut) + 4 + 11 * 8 > W - PAD.r;
    const anchor = (near && cut < ref) || overflow ? "end" : "start";
    const dx = anchor === "end" ? -4 : 4;
    parts.push(`<line class="chart-cut" stroke="${tone}" x1="${x(cut).toFixed(1)}" x2="${x(cut).toFixed(1)}" y1="${PAD.t}" y2="${H - PAD.b}"/>`);
    parts.push(`<text class="chart-mark" fill="${tone}" x="${(x(cut) + dx).toFixed(1)}" y="${PAD.t - 8}" text-anchor="${anchor}">CUTOFF ${cut.toFixed(1)}</text>`);
  }
  parts.push(`<line class="chart-cross" id="cross" x1="0" x2="0" y1="${PAD.t}" y2="${H - PAD.b}" visibility="hidden"/>`);
  parts.push(`<circle id="cross-dot" r="3" fill="#f2f2f0" visibility="hidden"/>`);
  parts.push(`<rect id="hit" x="${PAD.l}" y="0" width="${W - PAD.l - PAD.r}" height="${H}" fill="transparent"/>`);
  svg.innerHTML = parts.join("");

  const cross = svg.querySelector("#cross");
  const dot = svg.querySelector("#cross-dot");
  const resting = readout.textContent;
  const hit = svg.querySelector("#hit");
  hit.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const k = Math.min(nyq, Math.max(0, ((px - PAD.l) / (W - PAD.l - PAD.r)) * nyq));
    const i = Math.min(dbs.length - 1, Math.max(0, Math.floor((k / nyq) * dbs.length)));
    const xx = x(k).toFixed(1);
    cross.setAttribute("x1", xx); cross.setAttribute("x2", xx);
    dot.setAttribute("cx", xx); dot.setAttribute("cy", y(dbs[i]).toFixed(1));
    cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
    readout.textContent = `${k.toFixed(1)} kHz · ${signed(dbs[i] - (top - 10), 0)} dB`;
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden");
    readout.textContent = resting;
  });
  svg.setAttribute("aria-label", `Average spectrum from 0 to ${nyq.toFixed(1)} kHz${cut ? `, sound stops at ${cut} kHz` : ", sound reaches the top of the band"}`);
}
