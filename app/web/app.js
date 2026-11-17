// Bob Music - interface
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  results: null,      // última busca
  jobsByUrl: {},      // url -> job (para refletir status nos resultados)
  library: [],
  queue: [],          // fila do player (arquivos)
  playing: -1,
};

// ---------- utils ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtSize = (b) => (b / 1048576).toFixed(1) + " MB";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

// ---------- navegação ----------
function show(view) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  if (view === "library") loadLibrary();
  if (view === "settings") loadSettings();
}
$$(".nav-item").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

// ---------- busca ----------
const TYPE_LABEL = { search: "Resultados", track: "Música", album: "Álbum", playlist: "Playlist", artist: "Artista" };
const SOURCE_LABEL = { spotify: "Spotify", apple: "Apple Music" };
const sourceOf = (url) => (/music\.apple\.com/i.test(url || "") ? "apple" : "spotify");

state.source = "spotify";
try { if (localStorage.getItem("bm-source") === "apple") state.source = "apple"; } catch {}
function setSource(src) {
  state.source = src;
  $$(".source").forEach((b) => {
    const on = b.dataset.source === src;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on);
  });
  $("#search-input").placeholder = src === "apple"
    ? "Buscar na Apple Music ou colar link (music.apple.com/...)"
    : "Buscar no Spotify ou colar link (open.spotify.com/...)";
  try { localStorage.setItem("bm-source", src); } catch {}
}
$$(".source").forEach((b) => b.addEventListener("click", () => { setSource(b.dataset.source); $("#search-input").focus(); }));
setSource(state.source);

function skeleton(n = 8) {
  return Array.from({ length: n }, () => `
    <div class="track skeleton"><span></span><div class="cover"></div>
    <div class="t-main"><div class="t-title">.</div><div class="t-sub">.</div></div><span></span><span></span><span></span></div>`).join("");
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#search-input").value.trim();
  if (!q) return;
  $("#search-empty").hidden = true;
  $("#results-head").hidden = true;
  $("#results").innerHTML = skeleton();
  const btn = $("#search-form button");
  btn.disabled = true;
  try {
    // link colado decide a origem sozinho
    if (/music\.apple\.com/i.test(q)) setSource("apple");
    else if (/open\.spotify\.com/i.test(q)) setSource("spotify");
    state.results = await api(`/api/search?source=${state.source}&q=` + encodeURIComponent(q));
    renderResults();
  } catch (err) {
    $("#results").innerHTML = "";
    $("#search-empty").hidden = false;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

function trackAction(t) {
  const job = state.jobsByUrl[t.url];
  if (job) {
    if (job.status === "done" || job.status === "skipped") return `<span class="pill done">✓ Baixada</span>`;
    if (job.status === "queued") return `<span class="pill busy">Na fila</span>`;
    if (job.status === "running") return `<span class="pill busy">${job.progress}%</span>`;
  }
  return `<button class="pill" data-dl="${esc(t.url)}">⇣ Baixar</button>`;
}

function renderResults() {
  const r = state.results;
  if (!r) return;
  const list = $("#results");
  if (!r.tracks.length) {
    list.innerHTML = "";
    $("#results-head").hidden = true;
    $("#search-empty").hidden = false;
    $("#search-empty p").textContent = "Nada encontrado.";
    return;
  }
  $("#results-head").hidden = false;
  const src = r.source || "spotify";
  $("#list-type").innerHTML = `<span class="src-tag"><span class="dot ${src}"></span>${SOURCE_LABEL[src]} · ${TYPE_LABEL[r.type] || ""}</span>`;
  $("#list-name").textContent = r.type === "search" ? `“${r.name}”` : r.name;
  $("#list-count").textContent = `${r.tracks.length} música${r.tracks.length > 1 ? "s" : ""}`;
  const cover = $("#list-cover");
  cover.hidden = !r.cover;
  if (r.cover) cover.src = r.cover;
  $("#download-all span").textContent = r.type === "search" ? "Baixar todas" : "Baixar tudo";

  list.innerHTML = r.tracks.map((t, i) => `
    <div class="track" data-url="${esc(t.url)}">
      <span class="idx">${i + 1}</span>
      ${t.cover ? `<img class="cover" src="${esc(t.cover)}" loading="lazy" alt="">` : `<div class="cover">♪</div>`}
      <div class="t-main">
        <div class="t-title">${t.explicit ? '<span class="explicit">E</span>' : ""}${esc(t.title)}</div>
        <div class="t-sub">${esc(t.artist)}</div>
      </div>
      <div class="t-album">${esc(t.album)}</div>
      <div class="t-dur">${t.duration ? fmtTime(t.duration) : ""}</div>
      <div class="t-act">${trackAction(t)}</div>
    </div>`).join("");
}

function refreshResultActions() {
  if (!state.results) return;
  $$("#results .track").forEach((row) => {
    const t = state.results.tracks.find((x) => x.url === row.dataset.url);
    if (t) {
      const html = trackAction(t);
      const act = $(".t-act", row);
      if (act.innerHTML !== html) act.innerHTML = html;
    }
  });
}

async function download(tracks) {
  try {
    const { queued } = await api("/api/download", { method: "POST", body: { tracks } });
    toast(queued ? `${queued} música${queued > 1 ? "s" : ""} na fila de download` : "Já está na fila");
    pollJobs();
  } catch (err) {
    toast(err.message, true);
  }
}

$("#results").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-dl]");
  if (!btn) return;
  const t = state.results.tracks.find((x) => x.url === btn.dataset.dl);
  if (t) download([t]);
});
$("#download-all").addEventListener("click", () => {
  if (!state.results) return;
  const pending = state.results.tracks.filter((t) => {
    const j = state.jobsByUrl[t.url];
    return !j || j.status === "error" || j.status === "canceled";
  });
  if (pending.length) download(pending);
  else toast("Tudo já foi baixado ou está na fila");
});

// ---------- downloads ----------
const STATUS_ICON = { done: "✓", skipped: "✓", error: "!", canceled: "×" };

function renderJobs(jobs) {
  $("#jobs-empty").hidden = jobs.length > 0;
  $("#jobs").innerHTML = jobs.map((j) => `
    <div class="job ${j.status}">
      ${j.cover ? `<img class="cover" src="${esc(j.cover)}" alt="">` : `<div class="cover">♪</div>`}
      <div class="job-main">
        <div class="t-title">${esc(j.title || j.url)}</div>
        <div class="t-sub">${esc(j.artist)} <span class="src-tag">· <span class="dot ${sourceOf(j.url)}"></span>${SOURCE_LABEL[sourceOf(j.url)]}</span></div>
        <div class="bar"><i style="width:${j.progress}%"></i></div>
        <div class="job-line"><span class="status">${STATUS_ICON[j.status] || ""} ${esc(j.message)}</span><span>${j.status === "running" ? j.progress + "%" : ""}</span></div>
        ${j.error ? `<div class="job-err" title="${esc(j.error)}">${esc(j.error)}</div>` : ""}
      </div>
      <div class="job-act">
        ${j.status === "queued" ? `<button class="icon-btn" data-cancel="${j.id}" title="Cancelar"><svg viewBox="0 0 24 24"><path d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z"/></svg></button>` : ""}
        ${j.status === "error" || j.status === "canceled" ? `<button class="icon-btn" data-retry="${j.id}" title="Tentar de novo"><svg viewBox="0 0 24 24"><path d="M12 4a8 8 0 0 1 7.75 6h-2.08A6 6 0 1 0 18 14h2.05A8 8 0 1 1 12 4Zm5 0h3v6h-6V8h2.6A6 6 0 0 0 17 7.4V4Z"/></svg></button>` : ""}
        ${j.path ? `<button class="icon-btn" data-reveal="${esc(j.path)}" title="Mostrar na pasta"><svg viewBox="0 0 24 24"><path d="M3 5h7l2 2h9v12H3V5Zm2 4v8h14V9H5Z"/></svg></button>` : ""}
      </div>
    </div>`).join("");
}

$("#jobs").addEventListener("click", async (e) => {
  const c = e.target.closest("[data-cancel]");
  const r = e.target.closest("[data-retry]");
  const o = e.target.closest("[data-reveal]");
  if (c) await api(`/api/jobs/${c.dataset.cancel}/cancel`, { method: "POST" });
  if (r) await api(`/api/jobs/${r.dataset.retry}/retry`, { method: "POST" });
  if (o) await api("/api/open-folder", { method: "POST", body: { file: o.dataset.reveal } }).catch((err) => toast(err.message, true));
  pollJobs();
});
$("#clear-jobs").addEventListener("click", async () => { await api("/api/jobs/clear", { method: "POST" }); pollJobs(); });
$("#open-folder").addEventListener("click", () => api("/api/open-folder", { method: "POST", body: {} }));

let pollTimer = null;
let lastDone = 0;
async function pollJobs() {
  clearTimeout(pollTimer);
  try {
    const jobs = await api("/api/jobs");
    state.jobsByUrl = {};
    // a lista vem do mais novo para o mais antigo: o mais novo prevalece
    for (const j of [...jobs].reverse()) state.jobsByUrl[j.url] = j;
    renderJobs(jobs);
    refreshResultActions();
    const active = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
    const badge = $("#badge");
    badge.hidden = active === 0;
    badge.textContent = active;
    const done = jobs.filter((j) => j.status === "done").length;
    if (done > lastDone && $("#view-library").classList.contains("active")) loadLibrary();
    lastDone = done;
    pollTimer = setTimeout(pollJobs, active ? 800 : 4000);
  } catch {
    pollTimer = setTimeout(pollJobs, 3000);
  }
}

// ---------- biblioteca ----------
async function loadLibrary() {
  try {
    state.library = await api("/api/library");
    renderLibrary();
  } catch (err) { toast(err.message, true); }
}

function renderLibrary() {
  const f = $("#lib-filter").value.trim().toLowerCase();
  const items = state.library.filter((x) => !f || (x.title + " " + x.artist).toLowerCase().includes(f));
  $("#lib-empty").hidden = state.library.length > 0;
  const current = state.queue[state.playing]?.path;
  $("#library").innerHTML = items.map((x, i) => `
    <div class="track ${x.path === current ? "playing" : ""}" data-path="${esc(x.path)}">
      <span class="idx">${i + 1}</span>
      <img class="cover" src="/api/cover?file=${encodeURIComponent(x.path)}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'cover',textContent:'♪'}))">
      <div class="t-main">
        <div class="t-title">${esc(x.title)}</div>
        <div class="t-sub">${esc(x.artist)}</div>
      </div>
      <div class="t-album muted">${esc(x.ext.toUpperCase())} · ${fmtSize(x.size)}</div>
      <div class="t-dur"></div>
      <div class="t-act">
        <button class="icon-btn" data-reveal="${esc(x.path)}" title="Mostrar na pasta"><svg viewBox="0 0 24 24"><path d="M3 5h7l2 2h9v12H3V5Zm2 4v8h14V9H5Z"/></svg></button>
        <button class="icon-btn" data-del="${esc(x.path)}" title="Apagar"><svg viewBox="0 0 24 24"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-3 6h12l-1 12H7L6 9Z"/></svg></button>
      </div>
    </div>`).join("");
  state.libraryView = items;
}

$("#lib-filter").addEventListener("input", renderLibrary);
$("#lib-refresh").addEventListener("click", loadLibrary);
$("#library").addEventListener("click", async (e) => {
  const reveal = e.target.closest("[data-reveal]");
  const del = e.target.closest("[data-del]");
  if (reveal) { api("/api/open-folder", { method: "POST", body: { file: reveal.dataset.reveal } }); return; }
  if (del) {
    if (!confirm("Apagar este arquivo do computador?")) return;
    await api("/api/library?file=" + encodeURIComponent(del.dataset.del), { method: "DELETE" }).catch((err) => toast(err.message, true));
    loadLibrary();
    return;
  }
  const row = e.target.closest(".track");
  if (!row) return;
  const idx = state.libraryView.findIndex((x) => x.path === row.dataset.path);
  state.queue = state.libraryView.slice();
  play(idx);
});

// ---------- player ----------
const audio = $("#audio");
const PLAY = '<path d="M8 5v14l11-7L8 5Z"/>';
const PAUSE = '<path d="M6 5h4v14H6V5Zm8 0h4v14h-4V5Z"/>';

function play(i) {
  if (i < 0 || i >= state.queue.length) return;
  state.playing = i;
  const x = state.queue[i];
  audio.src = "/api/stream?file=" + encodeURIComponent(x.path);
  audio.play();
  $("#player").hidden = false;
  $("#np-title").textContent = x.title;
  $("#np-artist").textContent = x.artist;
  $("#np-cover").src = "/api/cover?file=" + encodeURIComponent(x.path);
  $$("#library .track").forEach((r) => r.classList.toggle("playing", r.dataset.path === x.path));
}
$("#play").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
$("#prev").addEventListener("click", () => (audio.currentTime > 3 ? (audio.currentTime = 0) : play(state.playing - 1)));
$("#next").addEventListener("click", () => play(state.playing + 1));
audio.addEventListener("ended", () => play(state.playing + 1));
audio.addEventListener("play", () => ($("#play-icon").innerHTML = PAUSE));
audio.addEventListener("pause", () => ($("#play-icon").innerHTML = PLAY));
audio.addEventListener("timeupdate", () => {
  $("#t-cur").textContent = fmtTime(audio.currentTime);
  $("#t-dur").textContent = fmtTime(audio.duration);
  if (!seeking && audio.duration) $("#seek").value = (audio.currentTime / audio.duration) * 1000;
});
let seeking = false;
$("#seek").addEventListener("input", () => (seeking = true));
$("#seek").addEventListener("change", (e) => {
  if (audio.duration) audio.currentTime = (e.target.value / 1000) * audio.duration;
  seeking = false;
});
$("#volume").addEventListener("input", (e) => (audio.volume = e.target.value / 100));
audio.volume = 0.8;
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) && audio.src) {
    e.preventDefault();
    audio.paused ? audio.play() : audio.pause();
  }
});

// ---------- configurações ----------
async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("#settings-form");
  form.format.innerHTML = s.formats.map((f) => `<option value="${f}">${f.toUpperCase()}</option>`).join("");
  form.bitrate.innerHTML = s.bitrates.map((b) => `<option value="${b}">${b === "auto" ? "Automático" : b.replace("k", " kbps")}</option>`).join("");
  form.output_dir.value = s.output_dir;
  form.format.value = s.format;
  form.bitrate.value = s.bitrate;
  form.lyrics.checked = s.lyrics;
  form.subfolders.checked = s.subfolders;
}
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/settings", {
      method: "POST",
      body: {
        output_dir: f.output_dir.value.trim(),
        format: f.format.value,
        bitrate: f.bitrate.value,
        lyrics: f.lyrics.checked,
        subfolders: f.subfolders.checked,
      },
    });
    await loadSettings();
    toast("Configurações salvas");
  } catch (err) { toast(err.message, true); }
});

pollJobs();
