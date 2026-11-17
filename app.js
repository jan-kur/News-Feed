"use strict";

const SECTIONS = [
  { key: "news", label: "News" },
  { key: "youtube", label: "YouTube" },
  { key: "reddit", label: "Reddit" },
];
const FEED_DIR = "data/feed/";

const $ = (sel) => document.querySelector(sel);
const state = { index: [], current: null, loadedKey: null };

// ---------- helpers

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function safeUrl(u) {
  try {
    const url = new URL(u, location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch { return "#"; }
}

function inline(text) {
  // text is already HTML-escaped
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) =>
      `<a href="${esc(safeUrl(u.replace(/&amp;/g, "&")))}" target="_blank" rel="noopener">${t}</a>`);
}

// Small, safe Markdown subset: paragraphs, - / 1. lists, **bold**, `code`, ```fences```, [links](url).
function md(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let para = [], list = null, code = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(esc(para.join(" ")))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(esc(i))}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (const line of lines) {
    if (code !== null) {
      if (/^\s*```/.test(line)) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; }
      else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) { flushPara(); flushList(); code = []; continue; }
    const ul = line.match(/^\s*[-*•]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push((ul || ol)[1]);
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else if (list && /^\s{2,}\S/.test(line)) {
      list.items[list.items.length - 1] += " " + line.trim();
    } else {
      flushList();
      para.push(line.replace(/^#+\s*/, "").trim());
    }
  }
  if (code !== null) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
  flushPara(); flushList();
  return out.join("");
}

function fmtMin(m) {
  if (m == null) return "";
  if (m < 1) return "<1 min";
  return `${Math.round(m)} min`;
}

function fmtDate(iso, opts = { month: "short", day: "numeric" }) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return isNaN(d) ? "" : d.toLocaleDateString(undefined, opts);
}

function todayLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

// ---------- rendering

function renderBudget(feed) {
  const isToday = feed.date === todayLocal();
  const label = isToday ? "Today" : fmtDate(feed.date, { weekday: "short", month: "short", day: "numeric" });
  const chips = SECTIONS.map((s) => {
    const sec = feed.sections?.[s.key];
    const n = sec?.items?.length || 0;
    return `<a class="chip" href="#sec-${s.key}">${s.label} ${n ? fmtMin(sec.minutes) : "–"}</a>`;
  }).join("");
  $("#budget").innerHTML =
    `<span class="total">${esc(label)}: ${esc(fmtMin(feed.total_minutes || 0))}</span>${chips}` +
    `<a class="chip" href="#x-list">+ X ${esc(feed.x_list_minutes ?? 5)} min</a>`;
}

function renderItem(it, sectionKey) {
  const meta = [esc(it.source), esc(fmtDate(it.published)), esc(fmtMin(it.minutes))].filter(Boolean);
  if (sectionKey === "youtube" && it.transcript === false) meta.push(`<span class="badge">no transcript</span>`);
  const links = [`<a href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener">Open original ↗</a>`];
  for (const l of it.links || []) {
    links.push(`<a href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`);
  }
  return `<article class="item">
    <h3><a href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener">${esc(it.title)}</a></h3>
    <div class="item-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
    <div class="summary">${md(it.summary)}</div>
    ${it.verdict ? `<div class="verdict">${inline(esc(it.verdict))}</div>` : ""}
    <div class="item-links">${links.join("")}</div>
  </article>`;
}

function renderFeed(feed) {
  renderBudget(feed);
  const parts = [];
  if (feed.intro) parts.push(`<p class="intro">${inline(esc(feed.intro))}</p>`);
  for (const s of SECTIONS) {
    const sec = feed.sections?.[s.key] || { items: [] };
    const items = sec.items || [];
    parts.push(`<section class="feed-section" id="sec-${s.key}">
      <div class="section-head"><h2>${s.label}</h2>
        <span class="meta">${items.length ? `${items.length} · ${fmtMin(sec.minutes)}` : ""}</span></div>
      ${items.length ? items.map((it) => renderItem(it, s.key)).join("") : `<p class="empty">Nothing worth your time today.</p>`}
    </section>`);
  }
  if (feed.x_list_url) {
    parts.push(`<a id="x-list" class="x-button" href="${esc(safeUrl(feed.x_list_url))}" target="_blank" rel="noopener">Open my X List</a>
      <p class="x-note">${esc(feed.x_list_minutes ?? 5)} minutes, not counted above.</p>`);
  }
  const more = feed.more || [];
  if (more.length) {
    parts.push(`<details class="more"><summary>More (${more.length}) · not counted</summary><ul>
      ${more.map((m) => `<li><a href="${esc(safeUrl(m.url))}" target="_blank" rel="noopener">${esc(m.title)}</a>
        <span class="src"> · ${esc(m.source || m.section || "")}</span></li>`).join("")}
    </ul></details>`);
  }
  const issues = feed.source_issues || [];
  if (issues.length) {
    parts.push(`<details class="issues"><summary>Source issues (${issues.length})</summary><ul>
      ${issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></details>`);
  }
  $("#main").innerHTML = parts.join("");
  const when = feed.finalized_at ? new Date(feed.finalized_at).toLocaleString() : "";
  $("#updated").textContent = when ? `Generated ${when}` : "";
}

function renderDayPicker() {
  const sel = $("#day");
  const days = state.index;
  sel.innerHTML = days.map((d) =>
    `<option value="${esc(d.date)}">${esc(fmtDate(d.date, { weekday: "short", month: "short", day: "numeric" }))} · ${esc(fmtMin(d.minutes))}</option>`
  ).join("");
  if (state.current) sel.value = state.current.date;
}

// ---------- loading

async function load({ quiet = false } = {}) {
  const wanted = decodeURIComponent(location.hash.replace(/^#(day=)?/, ""));
  const date = /^\d{4}-\d{2}-\d{2}$/.test(wanted) ? wanted : null;
  try {
    const [index, feed] = await Promise.all([
      getJSON(FEED_DIR + "index.json").catch(() => ({ days: [] })),
      getJSON(FEED_DIR + (date ? `${date}.json` : "latest.json")),
    ]);
    state.index = index.days || [];
    if (feed.date && !state.index.some((d) => d.date === feed.date)) {
      state.index.unshift({ date: feed.date, minutes: feed.total_minutes });
    }
    const key = `${feed.date}|${feed.finalized_at}`;
    if (key !== state.loadedKey) {
      state.current = feed;
      state.loadedKey = key;
      renderFeed(feed);
      if (!quiet) window.scrollTo(0, 0);
    }
    renderDayPicker();
  } catch (e) {
    if (!quiet) {
      $("#main").innerHTML = `<p class="status">No feed yet. ${esc(e.message)}</p>`;
      $("#budget").innerHTML = "";
    }
  }
}

$("#day").addEventListener("change", (e) => {
  const d = e.target.value;
  const latest = state.index[0]?.date;
  location.hash = d === latest ? "" : `#${d}`;
  if (d === latest) load();
});
window.addEventListener("hashchange", () => load());

// Section chips scroll without changing the URL hash (the hash holds the chosen day).
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="#sec-"], a[href="#x-list"]');
  if (!a) return;
  e.preventDefault();
  const target = document.getElementById(a.getAttribute("href").slice(1));
  if (target) {
    const offset = $(".top").getBoundingClientRect().height + 8;
    window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - offset, behavior: "smooth" });
  }
});

// Refresh when the app comes back to the foreground, so a new day's feed shows up.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") load({ quiet: true });
});

// ---------- theme

(function theme() {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch {}
  if (saved) root.dataset.theme = saved;
  $("#theme").addEventListener("click", () => {
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch {}
  });
})();

// ---------- service worker

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

load();
