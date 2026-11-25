// musicheck desktop app: list, filters, detail, player, organize.
import { api, ApiError, DESKTOP } from "./api.js";
import { renderChart } from "./chart.js";
import * as F from "./fmt.js";
import { Pulsar } from "./pulsar.js";

const $ = (id) => document.getElementById(id);
const ICON = (name) => `<svg class="i" aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;

const COLS = [
  { key: "check", cls: "check", label: "", width: "34px" },
  { key: "catno", label: "No.", width: "58px", sortKey: "path" },
  { key: "verdict", label: "Verdict", width: "110px" },
  { key: "title", label: "Title" },
  { key: "artist", label: "Artist", width: "16%", cls: "col-wide" },
  { key: "album", label: "Album", width: "15%", cls: "col-wide" },
  { key: "claimed", label: "Claimed", width: "116px" },
  { key: "real", label: "Real", width: "146px", cls: "col-real" },
  { key: "cutoff_khz", label: "Cutoff", width: "84px", num: true, cls: "col-cutoff" },
  { key: "dr", label: "DR", width: "44px", num: true, cls: "col-dr" },
  { key: "duration", label: "Time", width: "58px", num: true },
];

const S = {
  state: null,
  gen: -1,
  tracks: [],
  byId: new Map(),
  cat: new Map(),
  rows: [],
  spectra: {},
  sort: loadJSON("musicheck.sort", [["verdict", true], ["path", false]]),
  filter: { verdict: "ALL", format: "", real: "", q: "" },
  selected: null,
  checked: new Set(),
  detail: null,
  details: new Map(),
  playing: null,
  organizeIds: [],
};

function loadJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

// ---------------------------------------------------------------- toast

let toastTimer;
function toast(message, error = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("err", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 2400);
}

function fail(err) {
  toast(err instanceof ApiError ? err.message : `Something went wrong: ${err.message || err}`, true);
}

// ---------------------------------------------------------------- loading

async function poll() {
  let st;
  try {
    st = await api.state();
  } catch (err) {
    fail(err);
    setTimeout(poll, 3000);
    return;
  }
  S.state = st;
  renderHeader();
  renderScan();
  if (st.generation !== S.gen && !st.scan.running) await loadTracks();
  else if (!S.tracks.length) renderList();
  if (st.scan.running) setTimeout(poll, 400);
}

async function loadTracks() {
  const [data, spec] = await Promise.all([api.tracks(), api.spectra()]);
  S.gen = data.generation;
  S.tracks = data.tracks;
  S.byId = new Map(S.tracks.map((t) => [t.id, t]));
  const byPath = [...S.tracks].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  S.cat = new Map(byPath.map((t, i) => [t.id, F.catno(i + 1, byPath.length)]));
  S.spectra = spec.spectra;
  S.details.clear();
  for (const id of [...S.checked]) if (!S.byId.has(id)) S.checked.delete(id);
  renderFilters();
  applyView();
  if (!S.byId.has(S.selected)) select(S.rows[0]?.id ?? null, { scroll: true });
  else select(S.selected, { force: true });
}

// ---------------------------------------------------------------- header + scan

function renderHeader() {
  const st = S.state;
  const path = st.roots.length ? st.roots.join(", ").replace(st.home, "~") : "No folder open";
  $("folder-path").textContent = `\u200E${path}\u200E`;
  $("folder-btn").title = st.roots.length ? `${st.roots.join(", ")}\nOpen another folder (Ctrl+O)` : "Open a folder (Ctrl+O)";
  $("rescan").disabled = !st.roots.length || st.scan.running;
  document.title = st.roots.length ? `musicheck · ${path}` : "musicheck";
}

function renderStrip() {
  const counts = countBy(S.tracks, "verdict");
  const total = S.tracks.length;
  const strip = $("strip");
  if (!total) { strip.innerHTML = ""; strip.setAttribute("aria-label", "No tracks"); return; }
  strip.innerHTML = ["FAKE", "SUSPECT", "ERROR", "OK"].filter((v) => counts[v])
    .map((v) => `<span class="part" style="flex:${counts[v]}" title="${counts[v]} ${F.VERDICT_WORD[v].toLowerCase()}"><i style="background:${F.CODE[v]}"></i><b>${counts[v]}</b></span>`).join("");
  strip.setAttribute("aria-label", F.NAV_ORDER.filter((v) => counts[v]).map((v) => `${counts[v]} ${F.VERDICT_WORD[v].toLowerCase()}`).join(", "));
}

function renderScan() {
  const sc = S.state.scan;
  const bar = $("scanbar");
  bar.classList.toggle("error", Boolean(sc.error));
  if (sc.error && !sc.running) {
    bar.hidden = false;
    $("scan-label").textContent = "Scan failed";
    $("scan-readout").innerHTML = "";
    $("scan-count").textContent = "";
    $("scan-file").textContent = sc.error;
    return;
  }
  bar.hidden = !sc.running;
  if (!sc.running) return;
  const cells = 12;
  const frac = sc.total ? sc.done / sc.total : 0;
  const filled = Math.floor(frac * cells);
  $("scan-label").textContent = sc.total ? "Scanning" : "Looking for audio files";
  $("scan-readout").innerHTML = Array.from({ length: cells }, (_, i) =>
    `<i class="${i < filled ? "done" : i === filled && sc.total ? "now" : ""}"></i>`).join("");
  $("scan-count").textContent = sc.total ? `${sc.done} / ${sc.total}` : "";
  $("scan-file").textContent = sc.current || "";
}

// ---------------------------------------------------------------- filters

function countBy(list, key) {
  const out = {};
  for (const t of list) out[t[key]] = (out[t[key]] || 0) + 1;
  return out;
}

function renderFilters() {
  renderStrip();
  const counts = countBy(S.tracks, "verdict");
  const nav = $("verdict-nav");
  const items = [["ALL", "All", S.tracks.length], ...F.NAV_ORDER.map((v) => [v, F.VERDICT_WORD[v], counts[v] || 0])];
  nav.innerHTML = items.map(([v, word, n]) => `
    <button type="button" role="radio" data-v="${v}" aria-checked="${S.filter.verdict === v}"
      ${n === 0 && v !== "ALL" && S.filter.verdict !== v ? "disabled" : ""}>
      ${v === "ALL" ? "" : `<i class="code" style="background:${F.CODE[v]}" aria-hidden="true"></i>`}${word}<span class="n">${n}</span>
    </button>`).join("");

  const fmts = countBy(S.tracks, "format");
  const fsel = $("format-filter");
  fsel.innerHTML = `<option value="">All formats</option>` + Object.entries(fmts)
    .sort((a, b) => b[1] - a[1])
    .map(([f, n]) => `<option value="${F.esc(f)}">${F.esc(f || "unknown")} (${n})</option>`).join("");
  if (!(S.filter.format in fmts)) S.filter.format = "";
  fsel.value = S.filter.format;

  const reals = countBy(S.tracks, "real");
  const rsel = $("real-filter");
  const order = [...F.CLASSES].reverse().filter((c) => reals[c]);
  if (reals["?"]) order.push("?");
  rsel.innerHTML = `<option value="">Any quality</option>` + order
    .map((c) => `<option value="${F.esc(c)}">${c === "?" ? "Not judged" : F.esc(c)} (${reals[c]})</option>`).join("");
  if (!(S.filter.real in reals)) S.filter.real = "";
  rsel.value = S.filter.real;
}

function matches(t) {
  const f = S.filter;
  if (f.verdict !== "ALL" && t.verdict !== f.verdict) return false;
  if (f.format && t.format !== f.format) return false;
  if (f.real && t.real !== f.real) return false;
  if (f.q) {
    const hay = `${t.title} ${t.artist} ${t.album} ${t.path} ${t.genre}`.toLowerCase();
    return f.q.split(/\s+/).every((w) => hay.includes(w));
  }
  return true;
}

function sortValue(t, key) {
  switch (key) {
    case "verdict": return F.SEVERITY[t.verdict] ?? -1;
    case "title": return F.titleOf(t).toLowerCase();
    case "real": return F.realClass(t) < 0 ? null : F.realClass(t);
    case "claimed": return t.claimed ? t.claimed.toLowerCase() : null;
    case "path": return t.path.toLowerCase();
    default: {
      const v = t[key];
      if (F.blank(v)) return null;
      return typeof v === "string" ? v.toLowerCase() : v;
    }
  }
}

function sortRows(rows) {
  const keys = S.sort;
  return rows.sort((a, b) => {
    for (const [key, desc] of keys) {
      const va = sortValue(a, key), vb = sortValue(b, key);
      if (va === vb) continue;
      if (va === null) return 1;
      if (vb === null) return -1;
      const c = va < vb ? -1 : 1;
      return desc ? -c : c;
    }
    return 0;
  });
}

function applyView() {
  S.rows = sortRows(S.tracks.filter(matches));
  renderList();
  pulsar.setData(S.rows, S.spectra);
  renderAxis();
  if (!S.rows.some((t) => t.id === S.selected)) {
    select(S.rows[0]?.id ?? null, { scroll: true });
  } else {
    pulsar.setSelected(S.selected);
  }
  $("pulsar-empty").hidden = S.rows.length > 0;
  $("pulsar-empty").textContent = S.tracks.length
    ? "No track matches the filters."
    : "Spectra appear here once a folder is scanned.";
  $("shown-count").textContent = S.tracks.length
    ? (S.rows.length === S.tracks.length ? `${S.tracks.length} tracks` : `${S.rows.length} of ${S.tracks.length}`)
    : "";
  updateView();
}

// ---------------------------------------------------------------- list

function renderHead() {
  const primary = S.sort[0] || [];
  $("thead-row").innerHTML = COLS.map((c) => {
    const style = c.width ? ` style="width:${c.width}"` : "";
    const cls = [c.cls, c.num ? "num" : ""].filter(Boolean).join(" ");
    if (c.key === "check") {
      return `<th class="check" scope="col"${style}><input type="checkbox" id="check-all" aria-label="Tick every listed track"></th>`;
    }
    const key = c.sortKey || c.key;
    const on = primary[0] === key;
    const aria = on ? ` aria-sort="${primary[1] ? "descending" : "ascending"}"` : "";
    const icon = on ? ICON(primary[1] ? "down" : "up") : "";
    return `<th scope="col" class="${cls}"${style}${aria}><button type="button" data-sort="${key}">${c.label}${icon}</button></th>`;
  }).join("");
  syncCheckAll();
}

function cell(t, c) {
  switch (c.key) {
    case "check": return `<td class="check"><input type="checkbox" data-check="${t.id}" ${S.checked.has(t.id) ? "checked" : ""} aria-label="Tick ${F.esc(F.titleOf(t))}"></td>`;
    case "catno": return `<td class="catno">${S.cat.get(t.id)}</td>`;
    case "verdict": return `<td>${F.verdictTag(t.verdict)}</td>`;
    case "title": {
      const mark = S.playing === t.id ? `<svg class="playing-mark" aria-label="playing"><use href="/static/icons.svg#play"/></svg>` : "";
      const by = t.artist ? `<span class="by">${F.esc(t.artist)}</span>` : "";
      return `<td class="title" title="${F.esc(t.path)}">${F.esc(F.titleOf(t))}${mark}${by}</td>`;
    }
    case "real": return `<td class="col-real">${F.qcode(t)}<span class="qlabel${F.realClass(t) < 0 ? " dim" : ""}">${F.esc(F.realLabel(t))}</span></td>`;
    case "cutoff_khz": return `<td class="num col-cutoff">${F.blank(t.cutoff_khz) ? '<span class="dim">none</span>' : t.cutoff_khz.toFixed(1)}</td>`;
    case "dr": return `<td class="num col-dr">${F.blank(t.dr) ? "" : Math.round(t.dr)}</td>`;
    case "duration": return `<td class="num">${F.duration(t.duration)}</td>`;
    case "artist": return `<td class="col-wide" title="${F.esc(t.artist)}">${F.esc(t.artist)}</td>`;
    case "album": return `<td class="col-wide dim" title="${F.esc(t.album)}">${F.esc(t.album)}</td>`;
    case "claimed": return `<td title="${F.esc(t.claimed)}">${F.esc(t.claimed)}</td>`;
    default: return `<td title="${F.esc(t[c.key])}">${F.esc(t[c.key])}</td>`;
  }
}

function rowHTML(t) {
  return `<tr id="r-${t.id}" data-id="${t.id}" aria-selected="${t.id === S.selected}">${COLS.map((c) => cell(t, c)).join("")}</tr>`;
}

function renderList() {
  renderHead();
  $("tbody").innerHTML = S.rows.map(rowHTML).join("");
  renderEmpty();
}

function renderEmpty() {
  const el = $("empty");
  $("work").classList.toggle("bare", !S.tracks.length);
  const st = S.state;
  let html = "";
  if (!st) {
    html = "";
  } else if (!st.roots.length) {
    html = `<h3>Open a folder of music</h3>
      <p>musicheck decodes every file, finds where its sound really stops, and compares that with what the format claims: a FLAC made from an MP3, a 320 kbps file re-encoded from 128 kbps, a hi-res file upsampled from a CD. Measurements are cached, so reopening a folder takes a second.</p>
      <button type="button" class="btn" data-act="open">${ICON("folder")}<span>Open folder</span></button>`;
  } else if (!S.tracks.length && st.scan.running) {
    html = `<h3>Measuring the folder</h3>
      <p>Each file is decoded once, about a second per track on each core. The list fills in when the scan ends; files already measured come from the cache.</p>`;
  } else if (!S.tracks.length && S.gen >= 0) {
    html = `<h3>No audio files here</h3>
      <p>musicheck looks for MP3, FLAC, AAC and ALAC, Opus, Vorbis, WAV, AIFF, WavPack, APE and WMA files in this folder and its sub-folders.</p>
      <button type="button" class="btn" data-act="open">${ICON("folder")}<span>Open another folder</span></button>`;
  } else if (S.tracks.length && !S.rows.length) {
    html = `<h3>No track matches</h3>
      <p>Nothing in this folder fits the current verdict, format, quality and search filters.</p>
      <button type="button" class="btn" data-act="clear-filters">Clear filters</button>`;
  }
  el.innerHTML = html;
  el.hidden = !html;
}

function rowEl(id) {
  return id ? document.getElementById(`r-${id}`) : null;
}

function refreshRow(id) {
  const t = S.byId.get(id), el = rowEl(id);
  if (t && el) el.outerHTML = rowHTML(t);
}

function scrollToRow(id) {
  const el = rowEl(id), wrap = $("table-wrap");
  if (!el) return;
  const head = wrap.querySelector("thead").offsetHeight;
  const top = el.offsetTop - head, bottom = el.offsetTop + el.offsetHeight;
  if (top < wrap.scrollTop) wrap.scrollTop = top;
  else if (bottom > wrap.scrollTop + wrap.clientHeight) wrap.scrollTop = bottom - wrap.clientHeight;
}

function updateView() {
  const wrap = $("table-wrap");
  const first = $("tbody").firstElementChild;
  if (!first) { pulsar.setView(0, -1); return; }
  const h = first.offsetHeight || 33;
  const head = wrap.querySelector("thead").offsetHeight;
  const a = Math.floor(wrap.scrollTop / h);
  const b = Math.min(S.rows.length - 1, Math.floor((wrap.scrollTop + wrap.clientHeight - head) / h) - 1);
  pulsar.setView(a, Math.max(a, b));
}

// ---------------------------------------------------------------- selection

async function select(id, { scroll = false, force = false } = {}) {
  const prev = S.selected;
  if (id === prev && !force) return;
  S.selected = id;
  rowEl(prev)?.setAttribute("aria-selected", "false");
  rowEl(id)?.setAttribute("aria-selected", "true");
  if (id) $("table-wrap").setAttribute("aria-activedescendant", `r-${id}`);
  if (scroll) scrollToRow(id);
  pulsar.setSelected(id);
  if (!$("organize").hidden) return;
  await showDetail(id);
}

function moveSelection(delta) {
  if (!S.rows.length) return;
  const i = S.rows.findIndex((t) => t.id === S.selected);
  const j = Math.min(S.rows.length - 1, Math.max(0, (i < 0 ? 0 : i + delta)));
  select(S.rows[j].id, { scroll: true });
}

function toggleCheck(id, on) {
  if (on ?? !S.checked.has(id)) S.checked.add(id); else S.checked.delete(id);
  const box = document.querySelector(`input[data-check="${id}"]`);
  if (box) box.checked = S.checked.has(id);
  renderSelbar();
}

function visibleChecked() {
  return S.rows.filter((t) => S.checked.has(t.id)).map((t) => t.id);
}

function renderSelbar() {
  const n = visibleChecked().length;
  const hidden = S.checked.size - n;
  $("selbar").hidden = n === 0;
  $("sel-count").textContent = `${n} track${n === 1 ? "" : "s"} ticked` +
    (hidden > 0 ? ` (${hidden} more hidden by the filters, left out)` : "");
  syncCheckAll();
}

function syncCheckAll() {
  const box = $("check-all");
  if (!box) return;
  const shown = S.rows.filter((t) => S.checked.has(t.id)).length;
  box.checked = shown > 0 && shown === S.rows.length;
  box.indeterminate = shown > 0 && shown < S.rows.length;
}

// ---------------------------------------------------------------- detail

async function showDetail(id) {
  const el = $("detail");
  if (!id) {
    const why = !S.tracks.length ? "" : S.rows.length ? "Select a track to see why it got its verdict."
      : "No track matches the filters, so none is selected.";
    el.innerHTML = `<div class="blank">${why}</div>`;
    S.detail = null;
    return;
  }
  let d = S.details.get(id);
  if (!d) {
    try {
      d = await api.track(id);
    } catch (err) {
      if (S.selected === id) el.innerHTML = `<div class="blank">${F.esc(err.message)}</div>`;
      return;
    }
    S.details.set(id, d);
  }
  if (S.selected !== id) return;
  S.detail = d;
  renderDetail(d);
}

function dataRows(rows) {
  return rows.filter(([, v]) => !F.blank(v))
    .map(([k, v, mono]) => `<dt>${k}</dt><dd${mono ? ' class="mono"' : ""}>${F.esc(v)}</dd>`).join("");
}

function renderDetail(d) {
  const t = d.track, x = d.extra;
  const say = F.sentence(t, d.issues);
  const sub = [t.artist, t.album, t.year].filter(Boolean).join(" · ") || t.folder;
  const playingThis = S.playing === t.id && !audio.paused;
  const bits = t.bits ? `${t.bits} bit${t.eff_bits && t.eff_bits !== t.bits ? `, ${t.eff_bits} used` : ""}` : "";
  const encoder = [t.encoder, x.lame_vbr_quality != null ? `V${x.lame_vbr_quality}` : ""].filter(Boolean).join(" ");
  const reasons = (d.issues || []).filter((i) => !(i.level === t.verdict && say.text.startsWith(i.message.slice(0, 1).toUpperCase() + i.message.slice(1))));
  const ref = d.spectrum.reference_khz;
  const refNote = { expected: "", lossless: " (lossless floor)", "hi-res": " (hi-res floor)" }[d.spectrum.reference_label] ?? "";
  const issues = reasons.map((i) =>
    `<li><span class="lvl lvl-${i.level}">${i.level === "INFO" ? "NOTE" : i.level}</span><span>${F.esc(i.message)}</span></li>`).join("");
  $("detail").innerHTML = `
    <div class="d-head">
      <span class="d-cat" aria-label="Catalogue number ${S.cat.get(t.id)}">${S.cat.get(t.id)}</span>
      <div class="d-id">
        <div class="d-title">${F.esc(F.titleOf(t))}</div>
        <div class="d-sub">${F.esc(sub)}</div>
      </div>
      <div class="d-actions">
        <button type="button" class="btn listen${playingThis ? " playing" : ""}" id="listen" title="Play or pause (Space)">
          <span class="lbl">${ICON(playingThis ? "pause" : "play")}<span>${playingThis ? "Pause" : "Listen"}</span></span>
          <span class="time" id="listen-time">${F.duration(t.duration)}</span><i class="end" aria-hidden="true"></i>
        </button>
        <button type="button" class="btn small ghost" id="reveal" title="Show in the file manager">${ICON("reveal")}<span>Reveal</span></button>
        <button type="button" class="btn small ghost" id="copy-path" title="Copy the full path">${ICON("copy")}<span>Copy path</span></button>
        <button type="button" class="btn small" id="move-one" title="Move or copy this track">${ICON("move")}<span>Move…</span></button>
      </div>
      <p class="d-say"><b class="v-${t.verdict}">${F.esc(say.lead)}</b> ${F.esc(say.text)}</p>
      <div class="d-seek" ${S.playing === t.id ? "" : "hidden"}>
        <input type="range" class="seek" id="seek" min="0" max="1000" value="0" aria-label="Position">
        <span class="t" id="seek-time"></span>
      </div>
    </div>
    <div class="d-chart">
      <div class="panel-line"><h3 class="caps">Average spectrum <span class="dim">kHz · dB</span></h3><span class="readval" id="readval">${t.cutoff_khz ? `cutoff ${F.khz(t.cutoff_khz)}` : "no cutoff"}</span></div>
      <svg id="chart" role="img"></svg>
    </div>
    <div class="d-data">
      ${d.has_cover ? `<img class="d-cover" src="${api.coverUrl(t.id)}" alt="Cover of ${F.esc(t.album || F.titleOf(t))}">` : ""}
      ${issues ? `<h3 class="caps">Other findings</h3><ul class="issues">${issues}</ul>` : ""}
      <h3 class="caps">Quality</h3>
      <dl>${dataRows([
        ["Claimed", t.claimed], ["Real", F.realLabel(t)], ["Cutoff", t.cutoff_khz ? F.khz(t.cutoff_khz) : "none: full band"],
        ["Expected", ref ? `≥ ${F.khz(ref)}${refNote}` : ""], ["Encoder", encoder],
        ["LAME low-pass", F.khz(t.lowpass_khz)], ["Mode", t.mode]])}</dl>
      <h3 class="caps">Signal</h3>
      <dl>${dataRows([
        ["Sample rate", F.rate(t.sample_rate)], ["Bit depth", bits], ["Channels", t.channels === 1 ? "1, mono" : t.channels === 2 ? "2, stereo" : t.channels],
        ["Dynamic range", F.blank(t.dr) ? "" : `DR ${Math.round(t.dr)}`], ["Peak", F.db(t.peak_db, 2)], ["RMS", F.db(t.rms_db)],
        ["Clipped", F.blank(t.clip_pct) ? "" : `${t.clip_pct} %`], ["Side / mid", F.db(t.stereo_db)],
        ["Decode errors", t.decode_errors ? String(t.decode_errors) : ""]])}</dl>
      <h3 class="caps">Tags</h3>
      <dl>${dataRows([["Artist", t.artist], ["Album", t.album], ["Title", t.title], ["Track", t.track], ["Year", t.year],
        ["Genre", t.genre], ["BPM", t.bpm], ["Key", t.key]]) || "<dt>None</dt><dd></dd>"}</dl>
      <h3 class="caps">File</h3>
      <dl>${dataRows([["Format", t.format], ["Codec", t.codec], ["Size", F.mb(t.size_mb)], ["Duration", F.duration(t.duration)],
        ["Folder", t.folder === "." ? "" : t.folder, true], ["File", t.file, true]])}</dl>
    </div>`;
  renderChart($("chart"), $("readval"), d);
  chartObserver.disconnect();
  chartObserver.observe($("chart"));
  syncPlayer();
}

// ---------------------------------------------------------------- player

const audio = $("audio");

function play(id) {
  if (!id) return;
  if (S.playing === id) {
    if (audio.paused) audio.play().catch(playError); else audio.pause();
    return;
  }
  const prev = S.playing;
  S.playing = id;
  audio.src = api.audioUrl(id);
  audio.play().catch(playError);
  refreshRow(prev);
  refreshRow(id);
}

function playError(err) {
  if (err && err.name === "AbortError") return;
  const t = S.byId.get(S.playing);
  const what = t ? `${t.format || t.ext} file` : "file";
  toast(`This ${what} cannot be played here. The system may lack a GStreamer decoder for it.`, true);
  const was = S.playing;
  S.playing = null;
  refreshRow(was);
  syncPlayer();
}

function syncPlayer() {
  const t = S.detail?.track;
  const btn = $("listen");
  if (!t || !btn) return;
  const mine = S.playing === t.id;
  const on = mine && !audio.paused;
  btn.classList.toggle("playing", on);
  btn.querySelector(".lbl").innerHTML = `${ICON(on ? "pause" : "play")}<span>${on ? "Pause" : "Listen"}</span>`;
  const seekRow = document.querySelector(".d-seek");
  if (seekRow) seekRow.hidden = !mine;
  if (mine && audio.duration) {
    const p = audio.currentTime / audio.duration;
    const seek = $("seek");
    if (seek && !seek.matches(":active")) {
      seek.value = Math.round(p * 1000);
      seek.style.setProperty("--p", `${(p * 100).toFixed(2)}%`);
    }
    $("seek-time").textContent = `${F.duration(audio.currentTime)} / ${F.duration(audio.duration)}`;
    $("listen-time").textContent = F.duration(audio.currentTime);
  } else {
    $("listen-time").textContent = F.duration(t.duration);
  }
}

for (const ev of ["play", "pause", "timeupdate", "ended", "loadedmetadata"]) audio.addEventListener(ev, syncPlayer);
audio.addEventListener("error", () => { if (S.playing) playError(audio.error); });
audio.addEventListener("ended", () => { const was = S.playing; S.playing = null; refreshRow(was); syncPlayer(); });

// ---------------------------------------------------------------- actions

async function openFolder() {
  let path = null;
  try {
    if (DESKTOP) {
      path = (await api.pickFolder()).path;
    } else {
      path = window.prompt("Folder to open", S.state?.roots[0] || S.state?.home || "");
    }
    if (!path) return;
    S.selected = null;
    S.checked.clear();
    renderSelbar();
    S.state = await api.open(path);
    S.gen = -1;
    S.tracks = []; S.rows = [];
    applyView();
    showDetail(null);
    poll();
  } catch (err) {
    fail(err);
  }
}

async function rescan() {
  if (!S.state?.roots.length || S.state.scan.running) return;
  try {
    await api.scan();
    poll();
  } catch (err) {
    fail(err);
  }
}

async function copyPath(t) {
  try {
    await navigator.clipboard.writeText(t.abspath);
  } catch {
    const area = document.createElement("textarea");
    area.value = t.abspath;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("Path copied");
}

// ---------------------------------------------------------------- organize

function suggestDest() {
  const saved = localStorage.getItem("musicheck.dest");
  if (saved) return saved;
  const root = S.state?.root || S.state?.home || "";
  return root ? `${root.replace(/\/+$/, "")}-sorted` : "";
}

function openOrganize(ids) {
  if (!ids.length) return;
  S.organizeIds = ids;
  const n = ids.length;
  $("org-title").textContent = n === 1 ? `Move or copy “${F.titleOf(S.byId.get(ids[0]))}”` : `Move or copy ${n} tracks`;
  if (!$("org-dest").value) $("org-dest").value = suggestDest();
  $("org-browse").hidden = !DESKTOP;
  $("org-plan").hidden = true;
  $("org-note").textContent = "Nothing moves until you confirm the plan.";
  $("org-note").classList.remove("err");
  $("org-result").textContent = "";
  $("detail").hidden = true;
  $("organize").hidden = false;
  $("work").classList.add("organizing");
  $("org-dest").focus();
}

function closeOrganize() {
  $("organize").hidden = true;
  $("work").classList.remove("organizing");
  $("detail").hidden = false;
  showDetail(S.selected);
}

function orgRequest() {
  const tpl = $("org-template").value;
  return {
    ids: S.organizeIds,
    dest: $("org-dest").value.trim(),
    template: tpl === "custom" ? $("org-custom").value.trim() : tpl,
    mode: document.querySelector('input[name="mode"]:checked').value,
    keep_tree: $("org-keep").checked,
  };
}

const VERB = { move: "Move", copy: "Copy", link: "Link", hardlink: "Hard-link" };
const PAST = { move: "moved", copy: "copied", link: "linked", hardlink: "hard-linked" };

async function previewPlan(ev) {
  ev?.preventDefault();
  const req = orgRequest();
  const note = $("org-note");
  note.classList.remove("err");
  note.textContent = "Planning…";
  try {
    const plan = await api.plan(req);
    const dest = req.dest.replace(/\/+$/, "");
    const rel = (p) => (p.startsWith(`${dest}/`) ? p.slice(dest.length + 1) : p);
    const base = (p) => p.split("/").pop();
    $("plan-list").innerHTML = plan.moves.map((m) => `
      <div class="plan-row${m.skip ? " skip" : ""}"><span title="${F.esc(m.src)}">${F.esc(base(m.src))}</span><span class="arrow">${ICON("arrow")}</span>
      <span title="${F.esc(m.dst)}">${m.skip ? `<span class="why">skipped: ${F.esc(m.skip)}</span>` : F.esc(rel(m.dst))}</span></div>`).join("");
    const skipped = plan.moves.length - plan.todo;
    $("plan-sum").textContent = `${plan.todo} file${plan.todo === 1 ? "" : "s"} will be ${PAST[plan.mode]} into ${dest}` +
      (skipped ? `, ${skipped} skipped` : "") + ".";
    $("org-apply-label").textContent = `${VERB[plan.mode]} ${plan.todo} file${plan.todo === 1 ? "" : "s"}`;
    $("org-apply").disabled = plan.todo === 0;
    $("org-apply").classList.toggle("danger", plan.mode === "move");
    $("org-plan").hidden = false;
    $("org-apply").scrollIntoView({ block: "nearest" });
    note.textContent = "Check the plan, then confirm.";
    S.lastPlan = JSON.stringify(req);
  } catch (err) {
    note.textContent = err.message;
    note.classList.add("err");
    $("org-plan").hidden = true;
  }
}

async function applyPlan() {
  const req = orgRequest();
  if (JSON.stringify(req) !== S.lastPlan) { previewPlan(); return; }
  const btn = $("org-apply");
  btn.disabled = true;
  btn.classList.add("busy");
  try {
    const res = await api.apply(req);
    localStorage.setItem("musicheck.dest", req.dest);
    const msg = `${res.done} file${res.done === 1 ? "" : "s"} ${PAST[res.mode]}` +
      (res.errors.length ? `, ${res.errors.length} failed: ${res.errors.slice(0, 3).join("; ")}` : ".");
    $("org-result").textContent = msg;
    $("org-result").classList.toggle("err", res.errors.length > 0);
    toast(msg, res.errors.length > 0);
    if (req.mode === "move") for (const id of req.ids) S.checked.delete(id);
    renderSelbar();
    $("org-plan").hidden = res.errors.length === 0;
    if (!res.errors.length) setTimeout(closeOrganize, 900);
    poll();
  } catch (err) {
    $("org-result").textContent = err.message;
    $("org-result").classList.add("err");
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
  }
}

// ---------------------------------------------------------------- wiring

const pulsar = new Pulsar($("pulsar"), {
  onSelect: (id) => select(id, { scroll: true }),
  onHover: (h) => {
    const tip = $("pulsar-tip");
    if (!h) { tip.hidden = true; return; }
    const t = h.row;
    tip.innerHTML = `<span class="t">${S.cat.get(t.id)} · ${F.esc(F.titleOf(t))}</span>
      <span class="s">${F.esc(t.artist || t.folder)}</span>
      <span class="v">${F.verdictTag(t.verdict)}<span class="s">${t.cutoff_khz ? `stops at ${F.khz(t.cutoff_khz)}` : "full band"}</span></span>`;
    tip.hidden = false;
    const plot = $("pulsar-plot");
    const left = Math.min(plot.clientWidth - tip.offsetWidth - 8, Math.max(8, h.x));
    const top = h.y - tip.offsetHeight - 10 < 4 ? h.y + 12 : h.y - tip.offsetHeight - 10;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  },
});

function renderAxis() {
  const ticks = pulsar.ticks();
  $("pulsar-axis").innerHTML = ticks.map(({ k, frac }) => `<span style="left:${(frac * 100).toFixed(2)}%">${k}</span>`).join("");
  const top = ticks[ticks.length - 1].k;
  $("pulsar-scale").textContent = top > 24 ? `0 – 24 kHz, then to ${top}` : "0 – 24 kHz";
}
renderAxis();

$("folder-btn").addEventListener("click", openFolder);
$("rescan").addEventListener("click", rescan);
$("search").addEventListener("input", (e) => {
  S.filter.q = e.target.value.trim().toLowerCase();
  applyView();
});
$("format-filter").addEventListener("change", (e) => { S.filter.format = e.target.value; applyView(); });
$("real-filter").addEventListener("change", (e) => { S.filter.real = e.target.value; applyView(); });
$("verdict-nav").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-v]");
  if (!b || b.disabled) return;
  S.filter.verdict = b.dataset.v;
  renderFilters();
  applyView();
});

$("thead-row").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-sort]");
  if (b) {
    const key = b.dataset.sort;
    const [k0, d0] = S.sort[0] || [];
    const desc = k0 === key ? !d0 : ["verdict", "cutoff_khz", "dr", "duration"].includes(key);
    S.sort = [[key, desc], ...S.sort.filter(([k]) => k !== key)].slice(0, 3);
    localStorage.setItem("musicheck.sort", JSON.stringify(S.sort));
    applyView();
    scrollToRow(S.selected);
  }
});
$("thead-row").addEventListener("change", (e) => {
  if (e.target.id !== "check-all") return;
  const on = e.target.checked;
  for (const t of S.rows) if (on) S.checked.add(t.id); else S.checked.delete(t.id);
  for (const box of document.querySelectorAll("input[data-check]")) box.checked = S.checked.has(box.dataset.check);
  renderSelbar();
});

$("tbody").addEventListener("click", (e) => {
  const box = e.target.closest("input[data-check]");
  if (box) { toggleCheck(box.dataset.check, box.checked); return; }
  const tr = e.target.closest("tr[data-id]");
  if (tr) select(tr.dataset.id);
});
$("tbody").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr && !e.target.closest("input")) play(tr.dataset.id);
});
$("table-wrap").addEventListener("scroll", updateView, { passive: true });
new ResizeObserver(updateView).observe($("table-wrap"));

$("empty").addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "open") openFolder();
  if (act === "clear-filters") {
    S.filter = { verdict: "ALL", format: "", real: "", q: "" };
    $("search").value = "";
    renderFilters();
    applyView();
  }
});

$("detail").addEventListener("click", (e) => {
  const t = S.detail?.track;
  if (!t) return;
  const id = e.target.closest("button")?.id;
  if (id === "listen") play(t.id);
  if (id === "reveal") api.reveal(t.id).catch(fail);
  if (id === "copy-path") copyPath(t);
  if (id === "move-one") openOrganize([t.id]);
});
$("detail").addEventListener("input", (e) => {
  if (e.target.id === "seek" && audio.duration) {
    audio.currentTime = (e.target.value / 1000) * audio.duration;
    e.target.style.setProperty("--p", `${e.target.value / 10}%`);
  }
});
// Redraw the chart whenever its own box changes (window size, seek row shown or hidden).
let chartTimer;
const chartObserver = new ResizeObserver(() => {
  clearTimeout(chartTimer);
  chartTimer = setTimeout(() => { if (S.detail && $("chart")) renderChart($("chart"), $("readval"), S.detail); }, 40);
});

$("sel-organize").addEventListener("click", () => openOrganize(visibleChecked()));
$("sel-clear").addEventListener("click", () => {
  S.checked.clear();
  for (const box of document.querySelectorAll("input[data-check]")) box.checked = false;
  renderSelbar();
});
$("org-close").addEventListener("click", closeOrganize);
$("org-form").addEventListener("submit", previewPlan);
$("org-form").addEventListener("input", () => { $("org-plan").hidden = true; });
$("org-template").addEventListener("change", (e) => { $("org-custom-field").hidden = e.target.value !== "custom"; });
$("org-apply").addEventListener("click", applyPlan);
$("org-browse").addEventListener("click", async () => {
  try {
    const { path } = await api.pickFolder();
    if (path) { $("org-dest").value = path; $("org-plan").hidden = true; }
  } catch (err) { fail(err); }
});

document.addEventListener("keydown", (e) => {
  const typing = e.target.matches("input[type=text], input[type=search], select, textarea");
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key.toLowerCase() === "o") { e.preventDefault(); openFolder(); return; }
  if ((ctrl && e.key.toLowerCase() === "r") || e.key === "F5") { e.preventDefault(); rescan(); return; }
  if (ctrl && e.key.toLowerCase() === "f") { e.preventDefault(); $("search").focus(); $("search").select(); return; }
  if (e.key === "Escape") {
    if (!$("organize").hidden) { closeOrganize(); return; }
    if (e.target.id === "search" && e.target.value) { e.target.value = ""; S.filter.q = ""; applyView(); return; }
    if (typing) { e.target.blur(); return; }
    if (S.checked.size) { $("sel-clear").click(); }
    return;
  }
  if (typing) return;
  if (e.target.matches("input[type=range], button") && (e.key === " " || e.key === "Enter")) return;
  if (!$("organize").hidden && e.target.closest("#organize")) return;
  switch (e.key) {
    case "ArrowDown": case "j": e.preventDefault(); moveSelection(1); break;
    case "ArrowUp": case "k": e.preventDefault(); moveSelection(-1); break;
    case "PageDown": e.preventDefault(); moveSelection(10); break;
    case "PageUp": e.preventDefault(); moveSelection(-10); break;
    case "Home": e.preventDefault(); moveSelection(-1e9); break;
    case "End": e.preventDefault(); moveSelection(1e9); break;
    case " ": e.preventDefault(); play(S.selected); break;
    case "x": if (S.selected) toggleCheck(S.selected); break;
    case "/": e.preventDefault(); $("search").focus(); break;
    case "m": {
      const ticked = visibleChecked();
      const ids = ticked.length ? ticked : S.selected ? [S.selected] : [];
      openOrganize(ids);
      break;
    }
    default:
  }
});

renderHead();
showDetail(null);
poll();
