// The folder as a stack of spectra: one white line per track, in list order.
import { CODE } from "./fmt.js";

const BASE_KHZ = 24;     // linear scale up to here
const TAIL = 0.18;       // share of the width for a hi-res tail above BASE_KHZ
const PAD = { l: 40, r: 22, t: 14, b: 10 };
const INK = "#f2f2f0";
const BG = "#0b0b0c";
const GRID = "#1e1e22";
const SELECT_MS = 220;
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function mix(a, b, t) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(",")})`;
}

// Value of a ridge (0..1) at k kHz, linear between points.
function valueAt(pts, k) {
  if (!pts || !pts.length) return 0;
  if (k <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    if (pts[i][0] >= k) {
      const [k0, v0] = pts[i - 1], [k1, v1] = pts[i];
      return k1 === k0 ? v1 : v0 + ((v1 - v0) * (k - k0)) / (k1 - k0);
    }
  }
  return pts[pts.length - 1][1];
}

export class Pulsar {
  constructor(canvas, { onSelect, onHover }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.rows = [];
    this.spectra = {};
    this.selected = null;
    this.selectedAt = 0;
    this.hover = -1;
    this.view = [0, -1];
    this.maxKhz = BASE_KHZ;
    this.onSelect = onSelect;
    this.onHover = onHover;
    this.cache = new Map();
    new ResizeObserver(() => this.draw()).observe(canvas);
    canvas.addEventListener("mousemove", (e) => this.move(e));
    canvas.addEventListener("mouseleave", () => { this.hover = -1; this.draw(); this.onHover(null); });
    canvas.addEventListener("click", (e) => {
      const i = this.indexAt(e);
      if (i >= 0) this.onSelect(this.rows[i].id);
    });
  }

  setData(rows, spectra) {
    this.rows = rows;
    if (spectra !== this.spectra) this.cache.clear();
    this.spectra = spectra;
    const top = Math.max(0, ...rows.map((r) => (spectra[r.id]?.nyquist_hz || 0) / 1000));
    const max = top > BASE_KHZ + 0.5 ? Math.ceil(top / 8) * 8 : BASE_KHZ;
    if (max !== this.maxKhz) { this.maxKhz = max; this.cache.clear(); }
    this.draw();
  }

  setSelected(id) {
    if (id === this.selected) return;
    this.selected = id;
    this.selectedAt = performance.now();
    this.draw();
  }

  setView(first, last) {
    if (first === this.view[0] && last === this.view[1]) return;
    this.view = [first, last];
    this.draw();
  }

  // kHz -> x, with a compressed tail above 24 kHz when hi-res files are listed.
  xScale(w) {
    const x0 = PAD.l, span = w - PAD.l - PAD.r, max = this.maxKhz;
    const main = max > BASE_KHZ ? span * (1 - TAIL) : span;
    const x = (k) => (k <= BASE_KHZ ? x0 + (k / BASE_KHZ) * main
      : x0 + main + ((k - BASE_KHZ) / (max - BASE_KHZ)) * (span - main));
    const inv = (px) => {
      const d = px - x0;
      if (d <= main) return Math.max(0, (d / main) * BASE_KHZ);
      return Math.min(max, BASE_KHZ + ((d - main) / (span - main)) * (max - BASE_KHZ));
    };
    return { x, inv };
  }

  // Axis ticks as fractions of the plot width, for the HTML axis under the canvas.
  ticks() {
    const { x } = this.xScale(1000 + PAD.l + PAD.r);
    const ks = [0, 4, 8, 12, 16, 20, 24];
    if (this.maxKhz > BASE_KHZ) ks.push(this.maxKhz);
    return ks.map((k) => ({ k, frac: (x(k) - PAD.l) / 1000 }));
  }

  layout() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const n = Math.max(1, this.rows.length);
    const usable = h - PAD.t - PAD.b;
    // Ridges overlap like a pulse stack; a short list stays compact and centred.
    const step0 = n > 1 ? usable / (n + 1.4) : usable;
    const step = n > 1 ? Math.min(step0, 40) : 0;
    const amp = Math.min(96, Math.max(8, (n > 1 ? step : 40) * 2.4));
    const block = amp + (n - 1) * step;
    const top = PAD.t + Math.max(0, (usable - block) / 2);
    const base = (i) => top + amp + i * step;
    return { w, h, n, amp, step, base, ...this.xScale(w) };
  }

  // Ridge of one track: [kHz, 0..1] points, 0 at the noise floor.
  ridge(id) {
    if (this.cache.has(id)) return this.cache.get(id);
    const s = this.spectra[id];
    let pts = null;
    if (s && s.db && s.db.length) {
      const nyq = s.nyquist_hz / 1000;
      const m = s.db.length;
      let ref = -Infinity;
      s.db.forEach((v, i) => {
        const k = ((i + 0.5) / m) * nyq;
        if (k > 0.2 && k < 8 && v > ref) ref = v;
      });
      if (!Number.isFinite(ref)) ref = Math.max(...s.db);
      pts = [];
      for (let i = 0; i < m; i++) {
        const k = ((i + 0.5) / m) * nyq;
        if (k > this.maxKhz) break;
        const v = Math.min(1.08, Math.max(0, (s.db[i] - (ref - 84)) / 84));
        pts.push([k, v ** 1.35]);
      }
      if (nyq < this.maxKhz) pts.push([nyq, 0], [this.maxKhz, 0]);
    }
    this.cache.set(id, pts);
    return pts;
  }

  draw() {
    const c = this.canvas, ctx = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    const W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr);
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, c.clientWidth, c.clientHeight);
    if (!this.rows.length) return;
    const L = this.layout();
    const crisp = (v) => Math.round(v * dpr) / dpr + 0.5 / dpr;

    // kHz grid behind the ridges; the tail gets coarser lines
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1 / dpr;
    const grid = [];
    for (let k = 0; k <= BASE_KHZ; k += 2) grid.push(k);
    for (let k = BASE_KHZ + 8; k <= this.maxKhz; k += 8) grid.push(k);
    for (const k of grid) {
      ctx.beginPath();
      ctx.moveTo(crisp(L.x(k)), PAD.t);
      ctx.lineTo(crisp(L.x(k)), L.h - PAD.b);
      ctx.stroke();
    }

    const t = reduced() ? 1 : Math.min(1, (performance.now() - this.selectedAt) / SELECT_MS);
    const ease = 1 - (1 - t) ** 3;
    const dense = L.step < 4 && L.n > 1;
    const selIdx = this.rows.findIndex((r) => r.id === this.selected);
    for (let i = 0; i < this.rows.length; i++) {
      const row = this.rows[i];
      const b = L.base(i);
      const pts = this.ridge(row.id);
      const isSel = i === selIdx, isHover = i === this.hover;
      const head = b - (pts ? pts[0][1] : 0) * L.amp;
      if (!pts) {
        ctx.strokeStyle = "#3a3a3f";
        ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(L.x(0), b); ctx.lineTo(L.x(this.maxKhz), b); ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.beginPath();
        ctx.moveTo(L.x(0), b);
        for (const [k, v] of pts) ctx.lineTo(L.x(k), b - v * L.amp);
        ctx.lineTo(L.x(this.maxKhz), b);
        ctx.closePath();
        ctx.fillStyle = BG;
        ctx.fill();
        ctx.beginPath();
        pts.forEach(([k, v], j) => (j ? ctx.lineTo : ctx.moveTo).call(ctx, L.x(k), b - v * L.amp));
        ctx.lineJoin = "round";
        if (isSel) {
          ctx.strokeStyle = mix(INK, CODE[row.verdict] || INK, ease);
          ctx.lineWidth = 1.1 + 1.15 * ease;
        } else if (isHover) {
          ctx.strokeStyle = INK;
          ctx.lineWidth = 1.6;
        } else {
          ctx.strokeStyle = dense ? "rgba(242,242,240,0.62)" : "rgba(242,242,240,0.9)";
          ctx.lineWidth = dense ? 0.75 : 1.1;
        }
        ctx.stroke();
      }
      // verdict code tick at the head of its own line
      if (row.verdict !== "OK" || isSel) {
        const size = Math.max(2, Math.min(6, L.step > 0 ? L.step - 1 : 6));
        ctx.fillStyle = CODE[row.verdict] || INK;
        ctx.fillRect(PAD.l - 16, head - size / 2, size, size);
      }
    }
    if (t < 1) requestAnimationFrame(() => this.draw());

    // bracket: the rows visible in the track list
    const [first, last] = this.view;
    if (last >= first && L.n > 1 && (first > 0 || last < L.n - 1)) {
      const headOf = (i) => L.base(i) - ((this.ridge(this.rows[i].id) || [[0, 0]])[0][1]) * L.amp;
      const y0 = headOf(Math.max(0, first)) - 6;
      const y1 = headOf(Math.min(L.n - 1, last)) + 6;
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(14, y0); ctx.lineTo(8, y0); ctx.lineTo(8, y1); ctx.lineTo(14, y1);
      ctx.stroke();
    }
  }

  // The line drawn nearest the pointer at the pointer's frequency.
  pick(e) {
    if (!this.rows.length) return null;
    const r = this.canvas.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    const L = this.layout();
    const k = L.inv(px);
    if (L.n === 1) return { i: 0, k, L };
    const lo = Math.max(0, Math.floor((py - L.base(0)) / L.step) - 1);
    const hi = Math.min(L.n - 1, Math.ceil((py - L.base(0) + L.amp) / L.step) + 1);
    let best = -1, dist = Infinity;
    for (let i = lo; i <= hi; i++) {
      const y = L.base(i) - valueAt(this.ridge(this.rows[i].id), k) * L.amp;
      const d = Math.abs(py - y);
      if (d < dist) { dist = d; best = i; }
    }
    return best < 0 ? null : { i: best, k, L };
  }

  indexAt(e) {
    const p = this.pick(e);
    return p ? p.i : -1;
  }

  move(e) {
    const p = this.pick(e);
    const i = p ? p.i : -1;
    if (i !== this.hover) { this.hover = i; this.draw(); }
    if (!p) { this.onHover(null); return; }
    const pts = this.ridge(this.rows[i].id);
    const head = p.L.base(i) - (pts ? pts[0][1] : 0) * p.L.amp;
    // The tip hangs over the line's low-frequency head, away from its cutoff.
    this.onHover({ row: this.rows[i], khz: p.k, x: PAD.l + 8, y: head });
  }
}
