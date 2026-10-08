/* Walle Player web client: music saved on this device (IndexedDB) - plays without a connection to the computer.
   IndexedDB works over plain http too (the home network); opening the page itself offline needs the service worker,
   i.e. https (an external address).  Loaded before app.js; uses its helpers (api, toast, ...) at call time. */
"use strict";

const OFF = {
  db: null,
  ids: new Set(),            // ids of the tracks saved on this device
  queue: [], busy: false, done: 0, failed: 0, stop: false,
  urls: new Map(),           // track id -> object URL of the saved file (revoked when another track starts)

  req(r) { return new Promise((ok, bad) => { r.onsuccess = () => ok(r.result); r.onerror = () => bad(r.error); }); },
  tx(stores, mode = "readonly") { return this.db.transaction(stores, mode); },

  async init() {
    if (!("indexedDB" in window)) return;
    try {
      this.db = await new Promise((ok, bad) => {
        const r = indexedDB.open("walle-offline", 1);
        r.onupgradeneeded = () => {
          r.result.createObjectStore("tracks", { keyPath: "id" });   // {id, track, size, saved}
          r.result.createObjectStore("files");                       // id -> Blob (the audio file)
        };
        r.onsuccess = () => ok(r.result);
        r.onerror = () => bad(r.error);
      });
      const keys = await this.req(this.tx("tracks").objectStore("tracks").getAllKeys());
      this.ids = new Set(keys);
    } catch (e) { this.db = null; }
  },
  has(id) { return this.ids.has(id); },
  get available() { return !!this.db; },

  async list() {
    if (!this.db) return [];
    const rows = await this.req(this.tx("tracks").objectStore("tracks").getAll());
    return rows.sort((a, b) => b.saved - a.saved);
  },
  async usage() {
    const rows = await this.list();
    return { count: rows.length, bytes: rows.reduce((s, r) => s + (r.size || 0), 0) };
  },

  async fileUrl(id) {
    if (this.urls.has(id)) return this.urls.get(id);
    const blob = await this.req(this.tx("files").objectStore("files").get(id));
    if (!blob) return null;
    for (const [k, u] of this.urls) { URL.revokeObjectURL(u); this.urls.delete(k); }   // keep one file in memory
    const url = URL.createObjectURL(blob);
    this.urls.set(id, url);
    return url;
  },

  // ---- downloading ----------------------------------------------------------------------------------------------------
  download(tracks) {
    if (!this.db) return toast("Этот браузер не умеет сохранять музыку");
    const fresh = tracks.filter(t => t && !this.has(t.id) && !this.queue.some(q => q.id === t.id));
    if (!fresh.length) return toast(tracks.length > 1 ? "Всё уже скачано" : "Уже скачано");
    this.queue.push(...fresh);
    try { navigator.storage?.persist?.(); } catch {}   // ask the browser not to evict it (https only)
    this.run();
  },
  async run() {
    if (this.busy) return renderDownloads();
    this.busy = true; this.stop = false; this.done = 0; this.failed = 0;
    while (this.queue.length && !this.stop) {
      const t = this.queue[0];
      renderDownloads();
      try {
        const r = await fetch(t.stream, { credentials: "same-origin" });
        if (!r.ok) throw new Error(r.status === 410 ? "файла нет на компьютере" : "ошибка " + r.status);
        const blob = await r.blob();
        if (t.cover) fetch(t.cover).catch(() => {});             // warms the browser cache for the cover
        const tx = this.tx(["tracks", "files"], "readwrite");
        tx.objectStore("files").put(blob, t.id);
        tx.objectStore("tracks").put({ id: t.id, track: { ...t, wave: undefined }, size: blob.size, saved: Date.now() });
        await new Promise((ok, bad) => { tx.oncomplete = ok; tx.onerror = () => bad(tx.error); tx.onabort = () => bad(tx.error); });
        this.ids.add(t.id); this.done++;
        markOffline(t.id, true);
      } catch (e) {
        if (e?.name === "QuotaExceededError") { toast("На устройстве не хватает места — скачивание остановлено", 5000); this.queue = []; break; }
        if (e instanceof TypeError) { netDown(); toast("Нет связи — скачаю остальное, когда она появится", 4000); break; }
        this.failed++;
      }
      this.queue.shift();
    }
    this.busy = false;
    renderDownloads();
    if (!this.queue.length && this.done) toast(`Скачано на устройство: ${this.done}` + (this.failed ? `, не удалось: ${this.failed}` : ""));
  },
  cancel() { this.stop = true; this.queue = []; renderDownloads(); },

  // ---- fresh metadata without the audio: moods, genres, likes, the model's confidence -----------------------------------
  async refreshMeta(force = false) {
    if (!this.db || !this.ids.size) return 0;
    const last = store.get("wp_meta_refresh", 0);
    if (Date.now() - last < (force ? 30e3 : 10 * 60e3)) return 0;   // at start/reconnect every 10 min; the page: 30 s
    store.set("wp_meta_refresh", Date.now());
    const rows = await this.list();
    const fresh = new Map();
    for (let k = 0; k < rows.length; k += 2000) {
      const res = await api("/api/tracks/lookup", { ids: rows.slice(k, k + 2000).map(r => r.id) });
      for (const t of res.items) fresh.set(t.id, t);
    }
    const tx = this.tx("tracks", "readwrite");
    const st = tx.objectStore("tracks");
    let n = 0;
    for (const r of rows) {
      const t = fresh.get(r.id);
      if (t) { st.put({ ...r, track: t, refreshed: Date.now() }); n++; }
    }
    await new Promise(ok => { tx.oncomplete = ok; tx.onerror = ok; });
    const keys = ["moods", "mood_conf", "suggested", "genres", "liked", "flags", "lang", "vocal", "era", "plays", "last_played"];
    for (const q of P.queue) {                                      // the queue shows the fresh labels at once
      const t = fresh.get(q.id);
      if (t) for (const k of keys) q[k] = t[k];
    }
    store.set("wp_meta_time", Date.now());
    if (n) { renderPlayer(); P.save(); if (route.name === "offline") render(); }   // the page shows the fresh labels
    return n;
  },

  async remove(ids) {
    if (!this.db) return;
    const tx = this.tx(["tracks", "files"], "readwrite");
    for (const id of ids) { tx.objectStore("tracks").delete(id); tx.objectStore("files").delete(id); }
    await new Promise(ok => { tx.oncomplete = ok; tx.onerror = ok; });
    for (const id of ids) { this.ids.delete(id); markOffline(id, false); }
  },
};

// plays counted without a connection are sent later with their time (POST /api/plays accepts "at")
const PENDING = {
  add(p) { const l = store.get("wp_pending_plays", []); l.push(p); store.set("wp_pending_plays", l.slice(-2000)); },
  async flush() {
    const l = store.get("wp_pending_plays", []);
    if (!l.length) return;
    const left = [];
    for (const p of l) {
      try { await api("/api/plays", p); } catch (e) { if (e.message !== "no such track") left.push(p); }
    }
    store.set("wp_pending_plays", left);
  },
};

function fmtSize(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " ГБ" : Math.max(1, Math.round(bytes / 1e6)) + " МБ";
}
function markOffline(id, on) {
  document.querySelectorAll(`.row[data-id="${id}"] .off-mark`).forEach(m => m.hidden = !on);
}
function renderDownloads() {
  const bar = $("#dl");
  if (!bar) return;
  const left = OFF.queue.length;
  bar.hidden = !(OFF.busy && left);
  if (!bar.hidden) $("#dl-text").textContent = `Скачиваю на устройство: ${OFF.done + 1} из ${OFF.done + left}`;
}

// the app shell for opening the player without a connection (service workers need https or localhost)
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// ---- screens without a connection: only what can be played (the music saved on this device) ----------------------
const OFF_NOTE = `<p class="off-note"><svg><use href="#i-download"/></svg>Нет связи с компьютером — показано только скачанное на этом устройстве.</p>`;
const offTracks = async () => (await OFF.list()).map(r => r.track);
const offArtist = t => t.albumartist || t.artist || "";
const offCmp = (a, b) => String(a || "").localeCompare(String(b || ""), "ru", { sensitivity: "base" });
function offEmpty(text) {
  view.innerHTML = OFF_NOTE + `<div class="empty-note">${text}</div>`
    + (OFF.ids.size ? "" : `<div class="empty-note" style="padding-top:0">Чтобы слушать без связи, скачивайте треки, альбомы и плейлисты кнопкой «Скачать», пока связь есть.</div>`);
}
function offActions(getList) {         // «Слушать» / «Перемешать» only: everything here is already saved
  const acts = listActions(getList);
  [...acts.children].forEach(b => { if (b.textContent.includes("Скачать")) b.remove(); });
  return acts;
}
function offList(title, list, back = false) {
  setTop(title, back);
  view.innerHTML = OFF_NOTE + `<div id="o-actions"></div><div class="list" id="o-list">${trackRows(list)}</div>`;
  $("#o-actions").replaceChildren(offActions(() => list));
  bindRows($("#o-list"), () => list);
}

const OFFV = {
  async wave() {                       // the moods / genres that the saved music has; the wave plays from it
    const all = await offTracks();
    if (!all.length) { setTop("Моя волна", false); return offEmpty("На устройстве ничего не скачано — волне не из чего играть."); }
    const moods = INFO.moods.map(m => ({ ...m, n: all.filter(t => waveFits(t, { moods: [m.name] })).length })).filter(m => m.n);
    const gcount = new Map();
    for (const t of all) for (const g of t.genres || []) gcount.set(g, (gcount.get(g) || 0) + 1);
    const genres = [...gcount].map(([name, n]) => ({ name, n })).sort((a, b) => b.n - a.n);
    VIEWS.wave({ ...INFO, moods, genres });
    view.insertAdjacentHTML("afterbegin", OFF_NOTE);
  },

  async library() {
    setTop("Музыка", false);
    const all = await offTracks();
    if (!all.length) return offEmpty("На устройстве ничего не скачано.");
    const st = { ...{ q: "", order: "artist", mood: "", genre: "" }, ...store.get("wp_lib", {}) };
    const sorts = SORTS.filter(s => !["rare_random", "offline"].includes(s.key));
    if (!sorts.some(s => s.key === st.order)) st.order = "artist";
    const moods = INFO.moods.filter(m => all.some(t => (t.moods || []).includes(m.name)));
    const genres = [...new Set(all.flatMap(t => t.genres || []))].sort(offCmp);
    if (!moods.some(m => m.name === st.mood)) st.mood = "";
    if (!genres.includes(st.genre)) st.genre = "";
    view.innerHTML = OFF_NOTE + `
      <div class="search"><input id="q" type="search" placeholder="Название, исполнитель, жанр, настроение…" value="${esc(st.q)}" enterkeyhint="search"></div>
      <div class="chips scroll" id="sorts">${sorts.map(s => `<button class="chip" data-o="${s.key}">${s.name}</button>`).join("")}</div>
      <div class="actions" style="margin-top:10px">
        <select class="sel" id="f-mood"><option value="">Любое настроение</option>${moods.map(m => `<option>${esc(m.name)}</option>`).join("")}</select>
        <select class="sel" id="f-genre"><option value="">Любой жанр</option>${genres.map(g => `<option>${esc(g)}</option>`).join("")}</select>
      </div><div id="lib-actions"></div><div id="lib-list"></div>`;
    $("#f-mood").value = st.mood; $("#f-genre").value = st.genre;
    let shown = [];
    const by = {
      artist: (a, b) => offCmp(offArtist(a), offArtist(b)) || offCmp(a.album, b.album) || (a.track_no || 0) - (b.track_no || 0),
      title: (a, b) => offCmp(a.title, b.title), year: (a, b) => (b.year || 0) - (a.year || 0),
      play_count: (a, b) => (b.plays || 0) - (a.plays || 0), recent: (a, b) => (b.last_played || 0) - (a.last_played || 0),
      liked: (a, b) => offCmp(offArtist(a), offArtist(b)),
    };
    const run = () => {
      view.querySelectorAll("[data-o]").forEach(b => b.classList.toggle("on", b.dataset.o === st.order));
      const q = st.q.trim().toLocaleLowerCase();
      let l = all.filter(t => (!q || [t.title, t.artist, t.album, ...(t.genres || []), ...(t.moods || [])].join(" ").toLocaleLowerCase().includes(q))
        && (!st.mood || (t.moods || []).includes(st.mood)) && (!st.genre || (t.genres || []).includes(st.genre)));
      if (st.order === "liked") l = l.filter(t => t.liked);
      if (st.order === "recent") l = l.filter(t => t.last_played);
      if (st.order === "random") { l = l.slice(); for (let k = l.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [l[k], l[j]] = [l[j], l[k]]; } }
      else if (by[st.order]) l = l.slice().sort(by[st.order]);    // «Новые»: the order they were saved in
      shown = l;
      $("#lib-list").innerHTML = l.length ? `<div class="list">${trackRows(l)}</div>`
        : `<div class="empty-note">${st.order === "liked" ? "Среди скачанного нет любимых" : "Среди скачанного ничего не нашлось"}</div>`;
      if (l.length) bindRows($("#lib-list").firstElementChild, () => shown);
      $("#lib-actions").replaceChildren(offActions(() => shown));
    };
    let timer;
    $("#q").addEventListener("input", e => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value; run(); }, 200); });
    view.querySelectorAll("[data-o]").forEach(b => b.onclick = () => { st.order = b.dataset.o; run(); });
    $("#f-mood").onchange = e => { st.mood = e.target.value; run(); };
    $("#f-genre").onchange = e => { st.genre = e.target.value; run(); };
    run();
  },

  async artists() {
    setTop("Исполнители", false);
    const all = await offTracks();
    if (!all.length) return offEmpty("На устройстве ничего не скачано.");
    const n = new Map();
    for (const t of all) n.set(offArtist(t), (n.get(offArtist(t)) || 0) + 1);
    const items = [...n].map(([name, c]) => ({ name, n: c })).filter(a => a.name).sort((a, b) => offCmp(a.name, b.name));
    view.innerHTML = OFF_NOTE + `<div class="search"><input id="af" type="search" placeholder="Найти исполнителя"></div><div class="list" id="al"></div>`;
    const paint = f => {
      f = f.toLocaleLowerCase();
      $("#al").innerHTML = items.filter(a => !f || a.name.toLocaleLowerCase().includes(f)).map(a => `<button class="row" data-a="${esc(a.name)}"><span class="cover sm" style="display:grid;place-items:center;border-radius:50%">${esc(a.name.slice(0, 1).toUpperCase())}</span><span class="row-text"><b>${esc(a.name)}</b></span><span class="n">${a.n}</span></button>`).join("")
        || `<div class="empty-note">Никого не нашлось</div>`;
    };
    $("#al").onclick = e => { const b = e.target.closest("[data-a]"); if (b) go("artist/" + encodeURIComponent(b.dataset.a)); };
    $("#af").oninput = e => paint(e.target.value);
    paint("");
  },

  async artist(name) {
    const list = (await offTracks()).filter(t => offArtist(t) === name || t.artist === name)
      .sort((a, b) => offCmp(a.album, b.album) || (a.track_no || 0) - (b.track_no || 0));
    if (!list.length) { setTop(name, true); return offEmpty("Треков этого исполнителя на устройстве нет."); }
    offList(name, list, true);
  },

  async albums() {
    setTop("Альбомы", false);
    const all = await offTracks();
    const map = new Map();
    for (const t of all) {
      if (!t.album) continue;
      const k = offArtist(t) + "\x1f" + t.album;
      const a = map.get(k) || { artist: offArtist(t), name: t.album, year: t.year, cover: null, n: 0 };
      a.n++; a.cover ||= t.cover; map.set(k, a);
    }
    const items = [...map.values()].sort((a, b) => offCmp(a.artist, b.artist) || (a.year || 0) - (b.year || 0));
    if (!items.length) return offEmpty("Скачанных альбомов нет.");
    view.innerHTML = OFF_NOTE + `<div class="grid" id="ag">${items.map(a => `<button class="tile" data-ar="${esc(a.artist)}" data-al="${esc(a.name)}">
      <span class="cover ${a.cover ? "" : "empty"}" style="${a.cover ? `background-image:url('${a.cover}')` : ""}"></span>
      <b>${esc(a.name)}</b><small>${esc(a.artist)}${a.year ? " · " + a.year : ""} · ${a.n} трек.</small></button>`).join("")}</div>`;
    $("#ag").onclick = e => { const b = e.target.closest(".tile"); if (b) go(`album/${encodeURIComponent(b.dataset.ar)}/${encodeURIComponent(b.dataset.al)}`); };
  },

  async album(artist, name) {
    const list = (await offTracks()).filter(t => t.album === name && (offArtist(t) === artist || t.artist === artist))
      .sort((a, b) => (a.disc_no || 0) - (b.disc_no || 0) || (a.track_no || 0) - (b.track_no || 0));
    if (!list.length) { setTop(name, true); return offEmpty("Треков этого альбома на устройстве нет."); }
    offList(name, list, true);
  },

  async playlists() {                  // remembered while online; only those with saved tracks
    setTop("Плейлисты", false);
    const items = (store.get("wp_pl_list", []) || [])
      .map(p => ({ ...p, n: (store.get("wp_pl_" + p.id, null)?.ids || []).filter(id => OFF.has(id)).length })).filter(p => p.n);
    if (!items.length) return offEmpty("В плейлистах нет скачанных треков (плейлист запоминается, когда его открывают при связи).");
    view.innerHTML = OFF_NOTE + `<div class="list" id="pl">${items.map(p => `<button class="row" data-p="${p.id}"><span class="cover sm" style="display:grid;place-items:center">${icon(p.smart ? "wave" : "list")}</span>
      <span class="row-text"><b>${esc(p.name)}</b><small>скачано ${p.n}${p.total ? " из " + p.total : ""}</small></span></button>`).join("")}</div>`;
    $("#pl").onclick = e => { const b = e.target.closest("[data-p]"); if (b) go("playlist/" + b.dataset.p); };
  },

  async playlist(id) {
    const saved = store.get("wp_pl_" + id, null);
    const byId = new Map((await offTracks()).map(t => [t.id, t]));
    const list = (saved?.ids || []).map(i => byId.get(i)).filter(Boolean);
    if (!list.length) { setTop(saved?.name || "Плейлист", true); return offEmpty("Треков этого плейлиста на устройстве нет."); }
    offList(saved.name, list, true);
  },
};
