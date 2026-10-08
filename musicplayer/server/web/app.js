/* Walle Player web client: the library from the computer, played on this device.  No build step, no libraries. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = sec => { sec = Math.max(0, Math.floor(sec || 0)); const m = Math.floor(sec / 60), s = sec % 60; return `${m}:${String(s).padStart(2, "0")}`; };
const icon = (id, cls = "") => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const TABS = [
  { key: "wave", name: "Волна", icon: "wave" },
  { key: "library", name: "Музыка", icon: "music" },
  { key: "artists", name: "Исполнители", icon: "user" },
  { key: "albums", name: "Альбомы", icon: "album" },
  { key: "playlists", name: "Плейлисты", icon: "list" },
];
const SORTS = [
  { key: "artist", name: "По исполнителю" }, { key: "rare_random", name: "Случайно, чаще редкие" },
  { key: "random", name: "Случайно" }, { key: "added", name: "Новые", desc: true },
  { key: "liked", name: "♥ Любимые" }, { key: "offline", name: "Скачанное" }, { key: "play_count", name: "Часто слушаю", desc: true }, { key: "recent", name: "Недавние" },
  { key: "title", name: "По названию" }, { key: "year", name: "По году", desc: true },
];

let INFO = { moods: [], characters: [] };
let LOADED_VERSION = null;      // the Walle Player version this page came from: a newer one on the computer -> «Обновить»
const MOOD = {};   // name -> {color, glyph}

// ---- API -------------------------------------------------------------------------------------------------------
async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, { credentials: "same-origin", ...opts }); }
  catch (e) { NET.retryView = true; netDown(); throw new Error(NOCONN); }
  if (NET.down) netUp();
  if (r.status === 401) { showLogin(); throw new Error("401"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).error || m; } catch {} throw new Error(m); }
  return r.json();
}

function toast(text, ms = 2600) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// ---- player ------------------------------------------------------------------------------------------------------
const audio = $("#audio");
const P = {
  queue: [], i: -1, original: null, shuffle: false, repeat: "off",
  wave: null,                    // {moods, character} while «Моя волна» plays
  listened: 0, counted: false, last: null, fails: 0, loadingMore: false, loadGen: 0,

  get cur() { return this.queue[this.i] || null; },

  play(list, start = 0, opts = {}) {
    if (!list.length) return;
    this.wave = opts.wave || null;
    this.queue = list.slice();
    this.original = null;
    this.i = Math.max(0, Math.min(start, list.length - 1));
    if (this.shuffle && !this.wave) this.shuffleRest();
    this.load(true);
  },
  playNext(t) {
    if (!this.cur) return this.play([t]);
    this.queue.splice(this.i + 1, 0, t); toast("Будет следующим"); this.save(); renderQueueIfOpen();
  },
  enqueue(tracks) {
    if (!this.cur) return this.play(tracks);
    this.queue.push(...tracks); toast(tracks.length > 1 ? `В очереди: +${tracks.length}` : "Добавлено в очередь"); this.save(); renderQueueIfOpen();
  },
  shuffleRest() {
    this.original = this.queue.slice();
    const rest = this.queue.slice(this.i + 1);
    for (let k = rest.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [rest[k], rest[j]] = [rest[j], rest[k]]; }
    this.queue = [...this.queue.slice(0, this.i + 1), ...rest];
  },
  setShuffle(on) {
    this.shuffle = on;
    if (this.wave) { /* the wave chooses the order itself */ }
    else if (on) this.shuffleRest();
    else if (this.original) {
      const cur = this.cur, rest = new Set(this.queue.slice(this.i + 1).map(t => t.id));
      const before = this.queue.slice(0, this.i + 1);
      this.queue = [...before, ...this.original.filter(t => rest.has(t.id))];
      this.original = null;
      this.i = this.queue.indexOf(cur);
    }
    toast(on ? "Перемешивание включено" : "Перемешивание выключено");
    this.save(); renderPlayer(); renderQueueIfOpen();
  },
  cycleRepeat() {
    this.repeat = { off: "all", all: "one", one: "off" }[this.repeat];
    toast({ off: "Повтор выключен", all: "Повтор очереди", one: "Повтор трека" }[this.repeat]);
    this.save(); renderPlayer();
  },
  load(autoplay, pos = 0) {
    let t = this.cur;
    if (!t) return;
    if (NET.down && !OFF.has(t.id)) {           // no connection: the next track saved on the device
      const k = this.queue.findIndex((x, j) => j > this.i && OFF.has(x.id));
      if (k < 0) { toast("Нет связи, а дальше в очереди скачанных треков нет"); audio.pause(); renderPlayer(); return; }
      this.i = k; t = this.cur; pos = 0;
    }
    this.listened = 0; this.counted = false; this.last = null;
    const gen = ++this.loadGen;
    const go = src => {                          // a saved track plays from the device, the others stream
      if (gen !== this.loadGen) return;
      audio.src = src;
      if (pos) audio.addEventListener("loadedmetadata", () => { audio.currentTime = pos; }, { once: true });
      if (autoplay) audio.play().catch(() => {});
    };
    if (OFF.has(t.id)) OFF.fileUrl(t.id).then(u => go(u || t.stream), () => go(t.stream)); else go(t.stream);
    renderPlayer(); renderNowInViews(); mediaSession(t); this.save();
    if (this.wave && this.i >= this.queue.length - 3) this.moreWave();
  },
  toggle() {
    if (!this.cur) { if (this.queue.length) { this.i = 0; this.load(true); } return; }
    if (audio.paused) audio.play().catch(e => toast("Не удалось начать: " + e.message)); else audio.pause();
  },
  next(auto = false) {
    if (!this.queue.length) return;
    if (auto && this.repeat === "one") { audio.currentTime = 0; audio.play(); return; }
    if (this.i < this.queue.length - 1) this.i++;
    else if (this.repeat === "all" && !this.wave) this.i = 0;
    else if (this.wave) { this.moreWave(true); return; }
    else { if (auto) { audio.pause(); renderPlayer(); } return; }
    this.load(true);
  },
  prev() {
    if (audio.currentTime > 4 || this.i <= 0) { audio.currentTime = 0; return; }
    this.i--; this.load(true);
  },
  async moreWave(thenNext = false) {
    if (!this.wave || this.loadingMore) return;
    this.loadingMore = true;
    try {
      const res = await api("/api/wave/next", { ...this.wave, exclude: this.queue.map(t => t.id).slice(-400), n: 10 });
      this.queue.push(...res.items);
      if (thenNext && res.items.length) { this.i++; this.load(true); }
      renderQueueIfOpen(); this.save();
    } catch (e) {
      if (e.message === NOCONN && OFF.ids.size) {           // no connection: carry on with music saved on the device
        const add = await offlineTracks(10, new Set(this.queue.map(t => t.id)), this.wave);
        this.queue.push(...add);
        if (thenNext && add.length) { this.i++; this.load(true); }
        toast(add.length ? "Нет связи — играю скачанное под эту волну" : "Нет связи, а среди скачанного нет треков под эту волну");
      } else if (e.message !== "401") toast("Волна: " + e.message);
    }
    finally { this.loadingMore = false; }
  },
  tick() {                      // play counting: >50% of the track or 4 minutes, like on the computer
    const t = this.cur, pos = audio.currentTime;
    if (!t) return;
    if (!audio.paused && this.last != null) { const d = pos - this.last; if (d > 0 && d <= 3) this.listened += d; }
    this.last = pos;
    const dur = audio.duration || t.duration || 0;
    const limit = dur > 0 ? Math.min(dur * 0.5, 240) : 240;
    if (!this.counted && this.listened > limit) {
      this.counted = true;
      const play = { id: t.id, listened: Math.round(this.listened), at: Date.now() / 1000 };
      api("/api/plays", play).catch(e => { if (e.message === NOCONN) PENDING.add(play); });   // sent when back online
    }
  },
  save() {
    const lo = Math.max(0, this.i - 100);
    store.set("wp_state", { user: INFO.user?.name, queue: this.queue.slice(lo, lo + 400), i: this.i - lo, pos: audio.currentTime || 0,
      shuffle: this.shuffle, repeat: this.repeat, wave: this.wave });
  },
  restore() {
    const s = store.get("wp_state", null);
    if (!s || !s.queue?.length || (s.user && s.user !== INFO.user?.name)) return;   // another listener's queue
    Object.assign(this, { queue: s.queue, i: Math.min(s.i, s.queue.length - 1), shuffle: !!s.shuffle, repeat: s.repeat || "off", wave: s.wave || null });
    this.load(false, s.pos || 0);
  },
};

audio.addEventListener("timeupdate", () => { P.tick(); renderTime(); });
audio.addEventListener("play", () => { P.fails = 0; renderPlayState(); });
audio.addEventListener("pause", () => { renderPlayState(); P.save(); });
audio.addEventListener("ended", () => P.next(true));
audio.addEventListener("seeking", () => { P.last = null; });
audio.addEventListener("loadedmetadata", renderTime);
audio.addEventListener("error", async () => {
  if (!audio.src || !P.cur) return;
  P.resumeAt = audio.currentTime || P.resumeAt || 0;
  if (!(await serverReachable())) {                   // the connection, not the track: wait and continue from here
    P.resumeAfterNet = true;
    netDown();
    return;
  }
  P.fails++;
  toast(`Не удалось воспроизвести «${P.cur.title}»`);
  if (P.fails < 5) setTimeout(() => P.next(true), 900);
});

// ---- connection state: buffering, slow network, no connection ------------------------------------------------------
const NET = { down: false, timer: null, slow: null, retryView: false };
const NOCONN = "нет связи с компьютером";
async function serverReachable() {
  try { return (await fetch("/api/ping", { cache: "no-store" })).ok; } catch { return false; }
}
function showNet(text, kind) {
  const b = $("#net");
  b.textContent = text; b.className = "net " + kind; b.hidden = false;
  if (kind === "down" && OFF.ids.size) {
    const btn = document.createElement("button");
    btn.textContent = "Играть скачанное";
    btn.onclick = () => playOffline(true);
    b.append(" ", btn);
  }
}
function netDown() {
  if (NET.down) return;
  NET.down = true;
  showNet(navigator.onLine === false ? "Телефон не в сети — жду подключения…" : "Нет связи с компьютером — пробую снова…", "down");
  clearInterval(NET.timer);
  NET.timer = setInterval(async () => { if (await serverReachable()) netUp(); }, 3000);
  renderPlayer(); renderQueueIfOpen();
  if (OFF.available && OFFV[route.name] && $("#login").hidden && !NET.offlineView) {
    clearTimeout(NET.redraw);
    NET.redraw = setTimeout(() => render(), 0);       // this page again, from the music saved on the device
  }
}
function netUp() {
  if (!NET.down) return;
  NET.down = false;
  clearInterval(NET.timer);
  $("#net").hidden = true;
  toast("Связь восстановлена");
  PENDING.flush();
  if (OFF.queue.length) OFF.run();
  OFF.refreshMeta().catch(() => {});
  checkUpdate();
  renderPlayer(); renderQueueIfOpen();
  if (NET.retryView || NET.offlineView) { NET.retryView = false; render(); }   // the full page again
  if (P.resumeAfterNet && P.cur) { P.resumeAfterNet = false; P.load(true, P.resumeAt || 0); }
}
function setBuffering(on) {
  document.querySelectorAll('[data-act="toggle"], #wave-play').forEach(b => b.classList.toggle("buffering", on));
  clearTimeout(NET.slow);
  if (on) NET.slow = setTimeout(async () => {          // stuck for long: a slow network or a lost connection?
    if (!(await serverReachable())) { P.resumeAt = audio.currentTime; P.resumeAfterNet = true; netDown(); }
    else if (!NET.down) showNet("Медленная связь — трек подгружается…", "slow");
  }, 8000);
  else if (!NET.down) $("#net").hidden = true;
}
audio.addEventListener("waiting", () => setBuffering(true));
audio.addEventListener("stalled", () => { if (!audio.paused) setBuffering(true); });
audio.addEventListener("playing", () => setBuffering(false));
audio.addEventListener("pause", () => setBuffering(false));
audio.addEventListener("canplay", () => { if (audio.paused) setBuffering(false); });
audio.addEventListener("progress", renderBuffered);
window.addEventListener("offline", netDown);
window.addEventListener("online", async () => { if (await serverReachable()) netUp(); });
function renderBuffered() {
  const dur = audio.duration || 0, b = audio.buffered;
  let end = 0;
  for (let k = 0; k < b.length; k++) if (b.start(k) <= audio.currentTime + 1) end = Math.max(end, b.end(k));
  $("#seek").style.setProperty("--b", (dur ? Math.min(100, end / dur * 100) : 0) + "%");
}
setInterval(() => { if (!audio.paused) P.save(); }, 5000);
window.addEventListener("pagehide", () => P.save());

function mediaSession(t) {
  if (!("mediaSession" in navigator)) return;
  const art = t.cover ? [{ src: new URL(t.cover, location.href).href, sizes: "512x512", type: "image/jpeg" }]
                      : [{ src: new URL("/static/icon-512.png", location.href).href, sizes: "512x512", type: "image/png" }];
  navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.artist, album: t.album, artwork: art });
  const ms = navigator.mediaSession;
  const h = (a, f) => { try { ms.setActionHandler(a, f); } catch {} };
  h("play", () => audio.play()); h("pause", () => audio.pause());
  h("previoustrack", () => P.prev()); h("nexttrack", () => P.next());
  h("seekto", d => { audio.currentTime = d.seekTime; });
  h("seekbackward", d => { audio.currentTime = Math.max(0, audio.currentTime - (d.seekOffset || 10)); });
  h("seekforward", d => { audio.currentTime = audio.currentTime + (d.seekOffset || 10); });
}

// ---- «Моя волна» ratings ------------------------------------------------------------------------------------------
const canLabel = () => !!INFO.user?.can_label;     // labels teach the owner's mood model: the owner and allowed guests
// guesses are rated only in a wave with chosen moods: in «Все настроения» the question would be about a random mood
const isGuess = t => canLabel() && !NET.down && P.wave?.moods?.length > 0 && t?.wave && (t.wave.kind === "guess" || t.wave.kind === "auto") && t.wave.moods?.length;

async function rate(verdict) {
  const t = P.cur;
  if (!t) return;
  if (verdict === "fix") return openLabels(t);
  if (!isGuess(t)) { if (verdict === "dislike") P.next(); return; }
  try {
    const res = await api("/api/wave/rate", { id: t.id, verdict, kind: t.wave.kind, moods: t.wave.moods });
    if (verdict === "like") {
      t.moods = [...new Set([...(t.moods || []), ...t.wave.moods])];
      t.wave.kind = "known";
      toast(`Спасибо! «${res.moods.join(", ")}» — подтверждено, модель учтёт`);
      renderPlayer();
    } else {
      toast("Понял, это настроение больше не предложу");
      t.wave.kind = "known"; t.wave.moods = [];
      P.next();
    }
  } catch (e) { if (e.message !== "401") toast("Ошибка: " + e.message); }
}

// one sheet with every label of a track, as the desktop labelling feed: moods (fill = the model's confidence),
// recording type and voice, language, vocals, genre (families with their sub-genres + own), era
function openLabels(t) {
  const L = INFO.labels || { genre_tree: [], eras: [], flags: [], langs: [], vocals: [] };
  const st = { moods: new Set(t.moods || []), flags: new Set(t.flags || []), genres: [...(t.genres || [])],
               lang: t.lang || null, vocal: t.vocal || null, era: t.year ? null : (t.era || null) };
  const guessed = t.wave?.moods || [];
  const groups = {};
  for (const m of INFO.moods) (groups[m.group] ||= []).push(m);
  const has = g => st.genres.some(x => x.toLowerCase() === g.toLowerCase());
  const treeNames = new Set(L.genre_tree.flatMap(f => [f.name, ...f.subs]).map(x => x.toLowerCase()));
  const one = (key, items, cur) => items.map(i => `<button class="chip ${cur === i.key ? "on" : ""}" data-one="${key}" data-v="${esc(i.key)}">${esc(i.name)}</button>`).join("");
  const genresHtml = () => L.genre_tree.map(f => {
    const lit = has(f.name);
    return `<button class="chip" data-g="${esc(f.name)}" style="${lit ? `background:${f.color};color:#14171C;border-color:${f.color}` : `border-color:${f.color}66;color:${f.color};font-weight:600`}">${esc(f.name)}</button>`
      + f.subs.map(s => `<button class="chip ${has(s) ? "on" : ""}" data-g="${esc(s)}" data-fam="${esc(f.name)}" style="${has(s) ? "" : `color:${f.color}`}">${esc(s)}</button>`).join("");
  }).join("") + st.genres.filter(g => !treeNames.has(g.toLowerCase())).map(g => `<button class="chip on" data-g="${esc(g)}">${esc(g)}</button>`).join("");
  const html = `
    <p id="fix-note" class="fix-note" hidden>Заливка и проценты — насколько модель уверена в каждом настроении.</p>
    ${Object.entries(groups).map(([g, ms]) => `<div class="group-name">${esc(g)}</div><div class="chips">${
      ms.map(m => `<button class="chip ${st.moods.has(m.name) ? "on" : ""}" data-m="${esc(m.name)}"><span class="g" style="color:${m.color}">${esc(m.glyph)}</span>${esc(m.name)}</button>`).join("")}</div>`).join("")}
    <div class="group-name">Тип записи и голос</div>
    <div class="chips">${L.flags.map(f => `<button class="chip ${st.flags.has(f.key) ? "on" : ""}" data-f="${esc(f.key)}">${esc(f.name)}</button>`).join("")}</div>
    <div class="group-name">Язык</div><div class="chips">${one("lang", L.langs, st.lang)}</div>
    <div class="group-name">Голос</div><div class="chips">${one("vocal", L.vocals, st.vocal)}</div>
    <div class="group-name">Жанр</div><div class="chips" id="lab-genres">${genresHtml()}</div>
    <form class="genre-add" id="g-form"><input id="g-new" placeholder="Свой жанр" autocomplete="off"><button class="pill" type="submit">Добавить</button></form>
    <div class="group-name">Эпоха${t.year ? ` <span style="text-transform:none;letter-spacing:0">(год в файле: ${t.year} — он главнее)</span>` : ""}</div>
    <div class="chips">${one("era", L.eras.map(d => ({ key: d, name: `${d}-е` })), t.year ? Math.floor(t.year / 10) * 10 : st.era)}</div>
    <div class="actions">${isGuess(t) ? `<button class="pill ghost" id="fix-none">Подходящего настроения нет</button>` : ""}<button class="pill accent" id="fix-save">Сохранить</button></div>`;
  api(`/api/tracks/${t.id}/probs`).then(r => {
    const body = $("#sheet-content");
    for (const [m, p] of Object.entries(r.probs || {})) {
      const b = body.querySelector(`[data-m="${CSS.escape(m)}"]`);
      if (!b) continue;
      const pct = Math.round(p * 100);
      b.classList.add("prob"); b.style.setProperty("--p", pct + "%");
      b.insertAdjacentHTML("beforeend", ` <span class="pct">${pct}%</span>`);
    }
    if (Object.keys(r.probs || {}).length) $("#fix-note").hidden = false;
  }).catch(() => {});
  openSheet(`Разметить «${t.title}»`, html, body => {
    body.onclick = e => {                    // onclick, not addEventListener: #sheet-content is reused by every sheet
      const m = e.target.closest("[data-m]");
      if (m) { const k = m.dataset.m; st.moods.has(k) ? st.moods.delete(k) : st.moods.add(k); m.classList.toggle("on"); return; }
      const f = e.target.closest("[data-f]");
      if (f) { const k = f.dataset.f; st.flags.has(k) ? st.flags.delete(k) : st.flags.add(k); f.classList.toggle("on"); return; }
      const o = e.target.closest("[data-one]");
      if (o) {
        if (o.dataset.one === "era" && t.year) return toast("Эпоха берётся из года в файле");
        const key = o.dataset.one, v = key === "era" ? +o.dataset.v : o.dataset.v;
        st[key] = st[key] === v ? null : v;                    // a click on the lit value clears it
        o.parentElement.querySelectorAll("[data-one]").forEach(b => b.classList.toggle("on", st[key] !== null && String(st[key]) === b.dataset.v));
        return;
      }
      const g = e.target.closest("[data-g]");
      if (g) {
        const name = g.dataset.g;
        if (has(name)) st.genres = st.genres.filter(x => x.toLowerCase() !== name.toLowerCase());
        else { st.genres.push(name); if (g.dataset.fam && !has(g.dataset.fam)) st.genres.push(g.dataset.fam); }  // a sub-genre sets its family
        $("#lab-genres", body).innerHTML = genresHtml();
      }
    };
    $("#g-form", body).onsubmit = e => {
      e.preventDefault();
      const g = $("#g-new", body).value.trim();
      if (g && !has(g)) st.genres.push(g);
      $("#g-new", body).value = ""; $("#lab-genres", body).innerHTML = genresHtml();
    };
    const none = $("#fix-none", body);
    if (none) none.onclick = () => { closeSheet(); rate("dislike"); };
    $("#fix-save", body).onclick = async () => {
      const moods = [...st.moods];
      const payload = { moods, reject: guessed.filter(m => !st.moods.has(m)), genres: st.genres, flags: [...st.flags],
                        lang: st.lang, vocal: st.vocal };
      if (!t.year) payload.era = st.era;
      try {
        const res = await api(`/api/tracks/${t.id}/labels`, payload);
        for (const x of [t, ...P.queue]) if (x.id === t.id) Object.assign(x, { moods: res.moods, mood_conf: res.mood_conf,
          genres: res.genres, flags: res.flags, lang: res.lang, vocal: res.vocal, era: res.era });
        if (t.wave) { t.wave.kind = "known"; t.wave.moods = moods; }
        closeSheet(); toast("Сохранено — модель учтёт"); renderPlayer(); P.save();
      } catch (e) { if (e.message !== "401") toast("Ошибка: " + e.message); }
    };
  });
}

// ---- likes (the desktop app's «Избранное») ------------------------------------------------------------------------------
async function toggleLike(t) {
  if (!t) return;
  const on = !t.liked;
  try {
    await api("/api/favorites", { kind: "track", id: t.id, on });
    for (const x of [t, ...P.queue]) if (x.id === t.id) x.liked = on;
    document.querySelectorAll(`.row[data-id="${t.id}"] .liked-mark`).forEach(m => m.hidden = !on);
    toast(on ? "Добавлено в любимые" : "Убрано из любимых", 1600);
    renderPlayer(); P.save();
  } catch (e) { if (e.message !== "401") toast("Ошибка: " + e.message); }
}
async function likeGroup(btn, body) {
  const on = !btn.classList.contains("on");
  try {
    await api("/api/favorites", { ...body, on });
    btn.classList.toggle("on", on);
    btn.querySelector("use").setAttribute("href", on ? "#i-heart-on" : "#i-heart");
    toast(on ? "Добавлено в любимые" : "Убрано из любимых", 1600);
  } catch (e) { if (e.message !== "401") toast("Ошибка: " + e.message); }
}
function likePill(liked, body) {
  const b = document.createElement("button");
  b.className = "pill like-btn" + (liked ? " on" : "");
  b.innerHTML = `<svg><use href="#i-${liked ? "heart-on" : "heart"}"/></svg>Нравится`;
  b.onclick = () => likeGroup(b, body);
  return b;
}

// ---- player rendering -----------------------------------------------------------------------------------------------
function coverStyle(t) { return t?.cover ? `background-image:url('${t.cover}')` : ""; }
function coverEl(t, cls = "sm") { return `<span class="cover ${cls} ${t?.cover ? "" : "empty"}" style="${coverStyle(t)}"></span>`; }
function moodDots(t) {
  const ms = (t.moods || []).slice(0, 4);
  return ms.length ? `<span class="mood-dots">${ms.map(m => `<i style="background:${MOOD[m]?.color || "#888"}" title="${esc(m)}"></i>`).join("")}</span>` : "";
}

function renderPlayer() {
  const t = P.cur;
  $("#mini").hidden = !t;
  if (!t) return;
  const mc = $("#mini-cover");
  mc.className = `cover sm ${t.cover ? "" : "empty"}`; mc.style.backgroundImage = t.cover ? `url('${t.cover}')` : "";
  $("#mini-title").textContent = t.title;
  $("#mini-artist").textContent = t.artist;
  $("#mini-rate").hidden = !isGuess(t);
  $("#mini-like").hidden = (isGuess(t) && innerWidth < 900) || NET.down;
  document.querySelectorAll("[data-like]").forEach(b => { if (b.id !== "mini-like") b.hidden = NET.down; });
  for (const b of document.querySelectorAll("[data-like]")) {
    b.classList.toggle("on", !!t.liked);
    b.querySelector("use").setAttribute("href", t.liked ? "#i-heart-on" : "#i-heart");
    b.setAttribute("aria-label", t.liked ? "Убрать из любимых" : "Нравится");
  }
  const fc = $("#full-cover");
  fc.className = `full-cover ${t.cover ? "" : "empty"}`; fc.style.backgroundImage = t.cover ? `url('${t.cover}')` : "";
  $("#full-title").textContent = t.title;
  $("#full-artist").textContent = t.artist + (t.album ? " · " + t.album : "");
  $("#full-label").textContent = P.wave ? "Моя волна" : "Сейчас играет";
  const g = isGuess(t);
  $("#guess").hidden = !g;
  if (g) {
    const who = t.wave.kind === "auto" ? "Определено автоматически" : "Модель думает";
    const conf = t.wave.conf != null ? ` (${Math.round(t.wave.conf * 100)}%)` : "";
    $("#guess-text").innerHTML = `${who}: <b>«${esc(t.wave.moods.join(", "))}»</b>${conf}. Верно?`;
  }
  // a mood the model set by itself shows its confidence (percent and fill), like the desktop app
  $("#full-moods").innerHTML = (t.moods || []).map(m => {
    const c = t.mood_conf?.[m];
    const p = c != null ? Math.round(c * 100) : null;
    return `<span class="chip ${p != null ? "prob" : ""}" ${p != null ? `style="--p:${p}%" title="Определено моделью"` : ""}><span class="g" style="color:${MOOD[m]?.color || "#aaa"}">${esc(MOOD[m]?.glyph || "•")}</span>${esc(m)}${p != null ? ` <span class="pct">${p}%</span>` : ""}</span>`;
  }).join("")
    + (canLabel() && !NET.down ? `<button class="chip" id="mood-edit" style="color:var(--accent)">✎ Разметить</button>` : "");
  const me = $("#mood-edit"); if (me) me.onclick = () => openLabels(t);
  const gl = $("#full-genres");
  gl.innerHTML = (t.genres || []).length ? `Жанр: <b>${esc(t.genres.join(", "))}</b>${canLabel() ? " · изменить" : ""}` : (canLabel() ? "Указать жанр…" : "");
  gl.disabled = !canLabel();
  $("#btn-shuffle").classList.toggle("on", P.shuffle);
  const rp = $("#btn-repeat"); rp.classList.toggle("on", P.repeat !== "off"); rp.classList.toggle("one", P.repeat === "one");
  $("#full-wave").textContent = P.wave ? (P.wave.moods.length || P.wave.genres?.length ? "Волна: " + waveTitle(P.wave) : "Моя волна играет") : "AI-радио по настроению";
  renderPlayState(); renderTime();
}
function renderPlayState() {
  const playing = !audio.paused;
  document.querySelectorAll('[data-act="toggle"] use').forEach(u => u.setAttribute("href", playing ? "#i-pause" : "#i-play"));
  const wb = $("#wave-play use"); if (wb) wb.setAttribute("href", playing && P.wave ? "#i-pause" : "#i-play");
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
}
function renderTime() {
  const dur = audio.duration || P.cur?.duration || 0, pos = audio.currentTime || 0;
  const pct = dur ? (pos / dur) * 100 : 0;
  $("#mini-bar").style.width = pct + "%";
  if (!seeking) { const s = $("#seek"); s.value = Math.round(pct * 10); s.style.setProperty("--p", pct + "%"); }
  renderBuffered();
  $("#t-cur").textContent = fmt(pos); $("#t-dur").textContent = fmt(dur);
  if ("mediaSession" in navigator && dur && navigator.mediaSession.setPositionState) {
    try { navigator.mediaSession.setPositionState({ duration: dur, position: Math.min(pos, dur), playbackRate: 1 }); } catch {}
  }
}
function renderNowInViews() {
  const id = P.cur?.id;
  document.querySelectorAll(".row[data-id]").forEach(r => r.classList.toggle("playing", +r.dataset.id === id));
  if (route.name === "wave") renderWaveNow();
}

let seeking = false;
const seek = $("#seek");
seek.addEventListener("input", () => { seeking = true; const dur = audio.duration || 0; seek.style.setProperty("--p", seek.value / 10 + "%"); $("#t-cur").textContent = fmt(dur * seek.value / 1000); });
seek.addEventListener("change", () => { const dur = audio.duration || 0; if (dur) audio.currentTime = dur * seek.value / 1000; seeking = false; });

$(".mini-progress").addEventListener("click", e => {
  const r = e.currentTarget.getBoundingClientRect(), dur = audio.duration || 0;
  if (dur) audio.currentTime = dur * Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
});

const vol = $("#volume");
vol.value = store.get("wp_volume", 1); audio.volume = +vol.value; vol.style.setProperty("--p", vol.value * 100 + "%");
vol.addEventListener("input", () => { audio.volume = +vol.value; vol.style.setProperty("--p", vol.value * 100 + "%"); store.set("wp_volume", +vol.value); });

document.addEventListener("click", e => {
  const a = e.target.closest("[data-act]");
  if (a) {
    const act = a.dataset.act;
    if (act === "toggle") P.toggle(); else if (act === "next") P.next(); else if (act === "prev") P.prev();
    else if (act === "shuffle") P.setShuffle(!P.shuffle); else if (act === "repeat") P.cycleRepeat();
    else if (act === "queue") openQueue();
    return;
  }
  const r = e.target.closest("[data-rate]");
  if (r) { rate(r.dataset.rate); return; }
  if (e.target.closest("[data-like]")) toggleLike(P.cur);
});

// full player open / close (also by swiping down)
const full = $("#full");
function openFull() { full.classList.add("open"); full.setAttribute("aria-hidden", "false"); history.pushState({ full: 1 }, ""); }
function closeFull(fromPop) {
  if (!full.classList.contains("open")) return;
  full.classList.remove("open"); full.setAttribute("aria-hidden", "true");
  if (!fromPop && history.state?.full) history.replaceState(null, "");   // back() would race with a navigation that follows
}
$("#mini-open").onclick = openFull;
$("#full-close").onclick = () => closeFull();
$("#full-more").onclick = () => P.cur && trackMenu(P.cur, true);
$("#full-artist").onclick = () => { if (P.cur) { closeFull(); go(`artist/${encodeURIComponent(P.cur.albumartist || P.cur.artist)}`); } };
$("#full-genres").onclick = () => P.cur && openLabels(P.cur);
$("#full-wave").onclick = () => { closeFull(); go("wave"); };
(() => {
  let y0 = null;
  const head = $("#full-head");
  full.addEventListener("touchstart", e => { if (full.scrollTop <= 0 && (e.target.closest("#full-head") || e.target.closest("#full-cover"))) y0 = e.touches[0].clientY; }, { passive: true });
  full.addEventListener("touchmove", e => { if (y0 == null) return; const dy = e.touches[0].clientY - y0; if (dy > 0) full.style.transform = `translateY(${dy}px)`; }, { passive: true });
  full.addEventListener("touchend", e => {
    if (y0 == null) return; const dy = e.changedTouches[0].clientY - y0; y0 = null;
    full.style.transform = ""; if (dy > 110) closeFull();
  });
  head.addEventListener("dblclick", () => closeFull());
})();

// ---- sheets ---------------------------------------------------------------------------------------------------------
const sheet = $("#sheet");
function openSheet(title, html, bind) {
  $("#sheet-title").textContent = title;
  const c = $("#sheet-content"); c.innerHTML = html;
  sheet.classList.add("open"); sheet.setAttribute("aria-hidden", "false");
  if (bind) bind(c);
}
function closeSheet() {
  sheet.classList.remove("open"); sheet.setAttribute("aria-hidden", "true"); sheet.dataset.kind = "";
  $("#sheet-content").onclick = null;                         // a closed sheet keeps no handlers
}
sheet.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeSheet(); });

function trackMenu(t, fromPlayer = false) {
  const items = [
    !fromPlayer && ["play", "Играть следующим", () => P.playNext(t)],
    !fromPlayer && ["queue", "Добавить в очередь", () => P.enqueue([t])],
    !fromPlayer && !NET.down && [t.liked ? "heart-on" : "heart", t.liked ? "Убрать из любимых" : "Нравится", () => toggleLike(t)],
    OFF.available && (OFF.has(t.id) ? ["close", "Удалить с устройства", () => OFF.remove([t.id]).then(() => { toast("Удалено с устройства"); if (NET.down) render(); })]
                                     : !NET.down && ["download", "Скачать на устройство", () => OFF.download([t])]),
    ["user", "К исполнителю", () => { closeFull(); go(`artist/${encodeURIComponent(t.albumartist || t.artist)}`); }],
    t.album && ["album", "К альбому", () => { closeFull(); go(`album/${encodeURIComponent(t.albumartist || t.artist)}/${encodeURIComponent(t.album)}`); }],
    canLabel() && !NET.down && ["wave", "Разметить трек…", () => openLabels(t)],
    fromPlayer && ["queue", "Очередь", () => openQueue()],
  ].filter(Boolean);
  const meta = [t.album, t.year, (t.genres || []).join(", "), t.plays ? `слушал ${t.plays} раз` : "ещё не слушал"].filter(Boolean).join(" · ");
  openSheet(t.title, `<p style="color:var(--muted);margin:-6px 0 8px">${esc(t.artist)}${meta ? "<br>" + esc(meta) : ""}</p>` +
    items.map((it, k) => `<button class="menu-item" data-k="${k}">${icon(it[0])}${esc(it[1])}</button>`).join(""), body => {
    body.querySelectorAll("[data-k]").forEach(b => b.onclick = () => { const f = items[+b.dataset.k][2]; closeSheet(); setTimeout(f, 50); });
  });
}

function openQueue() {
  sheet.dataset.kind = "queue";
  openSheet(P.wave ? "Очередь волны" : "Очередь", queueHtml(), bindQueue);
  setTimeout(() => $("#sheet-content .row.playing")?.scrollIntoView({ block: "center" }), 60);
}
function queueHtml() {
  if (!P.queue.length) return `<div class="empty-note">Очередь пуста</div>`;
  const hide = (t, k) => NET.down && k !== P.i && !OFF.has(t.id);      // without a connection only the saved ones play
  return `<div class="list">${P.queue.map((t, k) => hide(t, k) ? "" : `<div class="row ${k === P.i ? "playing" : ""}" data-id="${t.id}" data-q="${k}">
    <button class="row-main" data-play="${k}">${coverEl(t)}<span class="row-text"><b>${esc(t.title)}</b><small>${esc(t.artist)}</small></span></button>
    ${k > P.i ? `<button class="icon-btn" data-del="${k}" aria-label="Убрать">${icon("close")}</button>` : ""}</div>`).join("")}</div>`;
}
function bindQueue(body) {
  body.querySelectorAll("[data-play]").forEach(b => b.onclick = () => { P.i = +b.dataset.play; P.load(true); renderQueueIfOpen(); });
  body.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { P.queue.splice(+b.dataset.del, 1); P.save(); renderQueueIfOpen(); });
}
function renderQueueIfOpen() {
  if (sheet.dataset.kind === "queue" && sheet.classList.contains("open")) { const c = $("#sheet-content"); const top = $(".sheet-body").scrollTop; c.innerHTML = queueHtml(); bindQueue(c); $(".sheet-body").scrollTop = top; }
}

// ---- track lists ------------------------------------------------------------------------------------------------------
function trackRows(list, offset = 0) {
  const cur = P.cur?.id;
  return list.map((t, k) => `<div class="row ${t.id === cur ? "playing" : ""}" data-id="${t.id}">
    <button class="row-main" data-i="${offset + k}">${coverEl(t)}<span class="row-text"><b>${esc(t.title)}</b>
      <small><span class="liked-mark" style="color:var(--accent)" ${t.liked ? "" : "hidden"}>♥ </span><span class="off-mark" title="Скачано на устройство" ${OFF.has(t.id) ? "" : "hidden"}>${icon("download")}</span>${esc(t.artist)}${t.duration ? " · " + fmt(t.duration) : ""}${moodDots(t)}</small></span></button>
    <button class="icon-btn" data-menu="${offset + k}" aria-label="Ещё">${icon("more")}</button></div>`).join("");
}
function bindRows(root, getList) {
  root.addEventListener("click", e => {
    const m = e.target.closest("[data-menu]"); if (m) { trackMenu(getList()[+m.dataset.menu]); return; }
    const b = e.target.closest("[data-i]"); if (b) P.play(getList(), +b.dataset.i);
  });
}

// paged list with infinite scroll; fetchPage(offset) -> {items, more}
function pagedList(container, fetchPage, onFirst) {
  const list = [];
  let more = true, busy = false, gen = 0;
  container.innerHTML = `<div class="list"></div><div class="loader">Загрузка…</div>`;
  const box = container.firstElementChild, loader = container.lastElementChild;
  bindRows(box, () => list);
  const load = async () => {
    if (busy || !more) return;
    busy = true; const my = gen;
    try {
      const res = await fetchPage(list.length);
      if (my !== gen) return;
      box.insertAdjacentHTML("beforeend", trackRows(res.items, list.length));
      list.push(...res.items); more = res.more;
      loader.textContent = more ? "Загрузка…" : (list.length ? "" : "Ничего не найдено");
      if (list.length === res.items.length && onFirst) onFirst(list);
    } catch (e) { if (e.message !== "401") loader.textContent = "Ошибка: " + e.message; }
    finally { busy = false; }
    if (more && loader.getBoundingClientRect().top < innerHeight + 600) load();
  };
  const io = new IntersectionObserver(es => { if (es.some(x => x.isIntersecting)) load(); }, { root: $("#view"), rootMargin: "600px" });
  io.observe(loader);
  load();
  return { list, stop() { gen++; io.disconnect(); } };
}

function listActions(getList) {
  const d = document.createElement("div");
  d.className = "actions";
  d.innerHTML = `<button class="pill accent">${icon("play")}Слушать</button><button class="pill">${icon("shuffle")}Перемешать</button>`
    + (OFF.available ? `<button class="pill" title="Сохранить на это устройство, чтобы слушать без связи">${icon("download")}Скачать</button>` : "");
  const [a, b, c] = d.children;
  if (c) c.onclick = () => { const l = getList(); if (l.length) OFF.download(l); };
  a.onclick = () => { const l = getList(); if (l.length) { P.shuffle = false; P.play(l, 0); } };
  b.onclick = () => { const l = getList(); if (l.length) { P.shuffle = true; P.play(l, Math.floor(Math.random() * l.length)); } };
  return d;
}

// ---- views ------------------------------------------------------------------------------------------------------------
const view = $("#view");
let route = { name: "wave", args: [] };
let cleanup = null;

function go(path) { location.hash = "#/" + path; }
function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  return { name: parts[0] || "wave", args: parts.slice(1) };
}
window.addEventListener("hashchange", () => render());
window.addEventListener("popstate", e => { if (full.classList.contains("open") && !e.state?.full) closeFull(true); });

function setTop(title, back) {
  $("#top-title").textContent = title;
  $("#btn-back").hidden = !back;
  $("#top-mark").hidden = !!back;
  document.title = title + " — Walle Player";
}
$("#btn-back").onclick = () => history.length > 1 ? history.back() : go("library");
$("#btn-search").onclick = () => { go("library"); setTimeout(() => $("#q")?.focus(), 60); };

function renderTabs() {
  const html = TABS.map(t => `<button class="tab" data-tab="${t.key}">${icon(t.icon)}<span>${t.name}</span></button>`).join("");
  $("#tabs").innerHTML = html; $("#side-nav").innerHTML = html;
  document.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => go(b.dataset.tab));
}
function markTab(name) {
  const map = { artist: "artists", album: "albums", playlist: "playlists" };
  const k = map[name] || name;
  document.querySelectorAll("[data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === k));
}

function render() {
  route = parseHash();
  if (cleanup) { cleanup(); cleanup = null; }
  closeSheet();
  markTab(route.name);
  view.scrollTop = 0;
  const offline = NET.down && OFF.available && OFFV[route.name];
  NET.offlineView = !!offline;
  if (NET.down) $("#net").hidden = !!offline;          // the page says it itself, the bar would cover its header
  const v = offline || VIEWS[route.name] || VIEWS.wave;
  Promise.resolve().then(() => v(...route.args)).catch(e => {
    if (e.message === NOCONN) return;                 // netDown() shows this page again from the saved music
    if (e.message !== "401") view.innerHTML = `<div class="empty-note">Ошибка: ${esc(e.message)}</div>`;
  });
}

const VIEWS = {
  wave(info = INFO) {
    if (typeof info !== "object" || !info) info = INFO;
    setTop("Моя волна", false);
    const ws = { moods: [], character: "mixed", genres: [], ...store.get("wp_wave", {}) };
    let allGenres = false;
    const groups = {};
    for (const m of info.moods) (groups[m.group] ||= []).push(m);
    view.innerHTML = `
      <div class="wave-hero">
        <button class="play-btn xl" id="wave-play" aria-label="Запустить волну">${icon("play")}</button>
        <h2 id="wave-head"></h2>
        <p id="wave-hint">${canLabel() ? "Трек за треком без конца по выбранным настроениям. Если модель не уверена в настроении трека, в плеере появится вопрос «Верно?» — ваш 👍 или 👎 её учит."
                        : "Трек за треком без конца — выберите настроение и характер."}</p>
        <div class="wave-now" id="wave-now" hidden></div>
      </div>
      <div class="group-name">Характер</div>
      <div class="seg" id="wave-char">${info.characters.map(c => `<button data-c="${c.key}">${esc(c.name)}</button>`).join("")}</div>
      ${Object.entries(groups).map(([g, ms]) => `<div class="group-name">${esc(g)}</div><div class="chips">${ms.map(m =>
        `<button class="chip" data-m="${esc(m.name)}"><span class="g" style="color:${m.color}">${esc(m.glyph)}</span>${esc(m.name)}${m.n ? ` <span class="cnt">${m.n}</span>` : ""}</button>`).join("")}</div>`).join("")}
      <div class="group-name">Жанры</div>
      <div class="chips" id="wave-genres"></div>
      <div class="actions" style="margin-top:18px"><button class="pill ghost" id="wave-reset">Сбросить выбор</button></div>`;
    const paintGenres = () => {
      const top = info.genres || [];
      const list = allGenres ? top : top.slice(0, 18);
      const extra = ws.genres.filter(g => !list.some(x => x.name === g)).map(name => ({ name, n: 0 }));
      $("#wave-genres").innerHTML = [...extra, ...list].map(g => `<button class="chip ${ws.genres.includes(g.name) ? "on" : ""}" data-g="${esc(g.name)}">${esc(g.name)}${g.n ? ` <span class="cnt">${g.n}</span>` : ""}</button>`).join("")
        + (top.length > 18 ? `<button class="chip" id="wave-more">${allGenres ? "Свернуть" : "Ещё жанры…"}</button>` : "");
    };
    $("#wave-genres").onclick = e => {
      if (e.target.closest("#wave-more")) { allGenres = !allGenres; paintGenres(); return; }
      const b = e.target.closest("[data-g]"); if (!b) return;
      const g = b.dataset.g; ws.genres = ws.genres.includes(g) ? ws.genres.filter(x => x !== g) : [...ws.genres, g];
      b.classList.toggle("on"); paint(); if (P.wave) startWave(ws);
    };
    const paint = () => {
      view.querySelectorAll("[data-m]").forEach(b => b.classList.toggle("on", ws.moods.includes(b.dataset.m)));
      view.querySelectorAll("[data-c]").forEach(b => b.classList.toggle("on", b.dataset.c === ws.character));
      $("#wave-head").textContent = waveTitle(ws);
      store.set("wp_wave", ws);
    };
    view.querySelectorAll("[data-m]").forEach(b => b.onclick = () => {
      const m = b.dataset.m; ws.moods = ws.moods.includes(m) ? ws.moods.filter(x => x !== m) : [...ws.moods, m]; paint();
      if (P.wave) startWave(ws);
    });
    view.querySelectorAll("[data-c]").forEach(b => b.onclick = () => { ws.character = b.dataset.c; paint(); if (P.wave) startWave(ws); });
    $("#wave-reset").onclick = () => { ws.moods = []; ws.genres = []; paint(); paintGenres(); if (P.wave) startWave(ws); };
    $("#wave-play").onclick = () => {
      if (P.wave && P.cur && sameWave(P.wave, ws)) P.toggle(); else startWave(ws);
    };
    paint(); paintGenres(); renderWaveNow(); renderPlayState();
  },

  library() {
    setTop("Музыка", false);
    const st = store.get("wp_lib", { q: "", order: "artist", mood: "", genre: "" });
    view.innerHTML = `
      <div class="search"><input id="q" type="search" placeholder="Название, исполнитель, жанр, настроение…" value="${esc(st.q)}" enterkeyhint="search"></div>
      <div class="chips scroll" id="sorts">${SORTS.map(s => `<button class="chip" data-o="${s.key}">${s.name}</button>`).join("")}</div>
      <div class="actions" style="margin-top:10px">
        <select class="sel" id="f-mood"><option value="">Любое настроение</option>${INFO.moods.map(m => `<option>${esc(m.name)}</option>`).join("")}</select>
        <select class="sel" id="f-genre"><option value="">Любой жанр</option>${(INFO.genres || []).map(g => `<option>${esc(g.name)}</option>`).join("")}</select>
      </div>
      <div id="lib-actions"></div>
      <div id="lib-list"></div>`;
    $("#f-mood").value = st.mood; $("#f-genre").value = st.genre;
    let pl = null;
    const run = () => {
      store.set("wp_lib", st);
      view.querySelectorAll("[data-o]").forEach(b => b.classList.toggle("on", b.dataset.o === st.order));
      pl?.stop();
      const box = $("#lib-list");
      if (st.order === "recent" || st.order === "liked") {
        box.innerHTML = `<div class="loader">Загрузка…</div>`;
        const holder = { list: [] };
        const src = st.order === "liked" ? api("/api/favorites").then(r => ({ items: r.tracks })) : api("/api/recent?limit=100");
        src.then(r => { holder.list = r.items; box.innerHTML = `<div class="list">${trackRows(r.items)}</div>` + (r.items.length ? "" : `<div class="empty-note">${st.order === "liked" ? "Пока ничего — нажмите ♥ у трека" : "Пока ничего не слушали"}</div>`); bindRows(box.firstElementChild, () => holder.list); });
        pl = { list: holder.list, stop() {} };
        $("#lib-actions").replaceChildren(listActions(() => holder.list));
        return;
      }
      const s = SORTS.find(x => x.key === st.order) || SORTS[0];
      const params = new URLSearchParams({ q: st.q, order: s.key, limit: 60 });
      if (s.desc) params.set("desc", "1");
      if (st.mood) params.set("mood", st.mood);
      if (st.genre) params.set("genre", st.genre);
      if (s.key.includes("random")) params.set("limit", 300);
      pl = pagedList(box, off => { params.set("offset", off); return api("/api/tracks?" + params); });
      $("#lib-actions").replaceChildren(listActions(() => pl.list));
    };
    let timer;
    $("#q").addEventListener("input", e => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value; run(); }, 250); });
    view.querySelectorAll("[data-o]").forEach(b => b.onclick = () => { if (b.dataset.o === "offline") return go("offline"); st.order = b.dataset.o; run(); });
    $("#f-mood").onchange = e => { st.mood = e.target.value; run(); };
    $("#f-genre").onchange = e => { st.genre = e.target.value; run(); };
    run();
    cleanup = () => pl?.stop();
  },

  async artists() {
    setTop("Исполнители", false);
    view.innerHTML = `<div class="search"><input id="af" type="search" placeholder="Найти исполнителя"></div><div class="list" id="al"><div class="loader">Загрузка…</div></div>`;
    const { items } = await api("/api/artists");
    const paint = f => {
      f = f.toLocaleLowerCase();
      const rows = items.filter(a => a.name && (!f || a.name.toLocaleLowerCase().includes(f))).slice(0, 400);
      $("#al").innerHTML = rows.map(a => `<button class="row" data-a="${esc(a.name)}"><span class="cover sm" style="display:grid;place-items:center;border-radius:50%">${esc(a.name.slice(0, 1).toUpperCase())}</span><span class="row-text"><b>${esc(a.name)}</b></span><span class="n">${a.n}</span></button>`).join("")
        || `<div class="empty-note">Никого не нашлось</div>`;
    };
    $("#al").onclick = e => { const b = e.target.closest("[data-a]"); if (b) go("artist/" + encodeURIComponent(b.dataset.a)); };
    $("#af").oninput = e => paint(e.target.value);
    paint("");
  },

  async artist(name) {
    setTop(name, true);
    view.innerHTML = `<div id="ar-actions"></div><div id="ar-list"><div class="loader">Загрузка…</div></div>`;
    const [r, fav] = await Promise.all([api("/api/tracks?" + new URLSearchParams({ artist: name, order: "album", limit: 500 })), api("/api/favorites")]);
    const acts = listActions(() => r.items);
    acts.append(likePill(fav.artists.includes(name), { kind: "artist", name }));
    $("#ar-actions").replaceChildren(acts);
    $("#ar-list").innerHTML = `<div class="list">${trackRows(r.items)}</div>`;
    bindRows($("#ar-list").firstElementChild, () => r.items);
  },

  async albums() {
    setTop("Альбомы", false);
    view.innerHTML = `<div class="search"><input id="alf" type="search" placeholder="Альбом или исполнитель"></div><div class="grid" id="ag"></div><div class="loader" id="al-more"></div>`;
    const { items } = await api("/api/albums");
    let shown = [], n = 0;
    const grid = $("#ag");
    const more = () => {
      const chunk = shown.slice(n, n + 60); n += chunk.length;
      grid.insertAdjacentHTML("beforeend", chunk.map(a => `<button class="tile" data-ar="${esc(a.artist)}" data-al="${esc(a.name)}">
        <span class="cover ${a.cover ? "" : "empty"}" style="${a.cover ? `background-image:url('${a.cover}')` : ""}"></span>
        <b>${esc(a.name)}</b><small>${esc(a.artist)}${a.year ? " · " + a.year : ""}</small></button>`).join(""));
      $("#al-more").textContent = n < shown.length ? "…" : (shown.length ? "" : "Ничего не найдено");
    };
    const paint = f => {
      f = f.toLocaleLowerCase();
      shown = items.filter(a => !f || a.name.toLocaleLowerCase().includes(f) || (a.artist || "").toLocaleLowerCase().includes(f));
      grid.innerHTML = ""; n = 0; more();
    };
    grid.onclick = e => { const b = e.target.closest(".tile"); if (b) go(`album/${encodeURIComponent(b.dataset.ar)}/${encodeURIComponent(b.dataset.al)}`); };
    $("#alf").oninput = e => paint(e.target.value);
    const io = new IntersectionObserver(es => { if (es.some(x => x.isIntersecting) && n < shown.length) more(); }, { root: view, rootMargin: "600px" });
    io.observe($("#al-more"));
    cleanup = () => io.disconnect();
    paint("");
  },

  async album(artist, name) {
    setTop(name, true);
    view.innerHTML = `<div class="loader">Загрузка…</div>`;
    const [r, fav] = await Promise.all([api("/api/tracks?" + new URLSearchParams({ album: name, artist, order: "album", limit: 500 })), api("/api/favorites")]);
    const t0 = r.items[0];
    view.innerHTML = `<div style="display:flex;gap:16px;align-items:flex-end;margin:4px 0 14px">${coverEl(t0, "")}
      <div><div style="color:var(--muted)">${esc(artist)}</div><div style="color:var(--dim);font-size:13px">${[t0?.year, r.items.length + " трек."].filter(Boolean).join(" · ")}</div></div></div>
      <div id="alb-actions"></div><div class="list" id="alb-list">${trackRows(r.items)}</div>`;
    const c = view.querySelector(".cover"); c.style.width = c.style.height = "120px"; c.style.borderRadius = "12px";
    const acts = listActions(() => r.items);
    acts.append(likePill(fav.albums.some(a => a.artist === artist && a.name === name), { kind: "album", artist, album: name }));
    $("#alb-actions").replaceChildren(acts);
    bindRows($("#alb-list"), () => r.items);
  },

  async offline() {
    setTop("Скачанное", true);
    view.innerHTML = `<div class="loader">Загрузка…</div>`;
    const rows = await OFF.list();
    const list = rows.map(r => r.track);
    const bytes = rows.reduce((s, r) => s + (r.size || 0), 0);
    const when = store.get("wp_meta_time", 0);
    const ago = when ? Math.round((Date.now() - when) / 60000) : null;
    view.innerHTML = `<p style="color:var(--muted);margin:0 0 10px">${rows.length ? `${rows.length} трек. · ${fmtSize(bytes)} на этом устройстве. Играют без связи с компьютером.
        <br><small style="color:var(--dim)">Настроения и жанры ${ago === null ? "ещё не обновлялись" : ago < 1 ? "обновлены только что" : `обновлены ${ago} мин назад`} — обновляются сами при связи, без перекачки музыки.</small>`
        : "Здесь будет музыка, сохранённая на это устройство: «Скачать» у трека, альбома, плейлиста или в «Любимых»."}</p>
      <div id="off-actions"></div><div class="list" id="off-list">${trackRows(list)}</div>`;
    if (rows.length) {
      const acts = listActions(() => list);
      acts.lastElementChild.remove();                           // «Скачать» makes no sense here
      const del = document.createElement("button");
      del.className = "pill ghost"; del.textContent = "Удалить всё";
      del.onclick = async () => { if (confirm(`Удалить с устройства ${rows.length} трек. (${fmtSize(bytes)})?`)) { await OFF.remove(list.map(t => t.id)); render(); } };
      acts.append(del);
      $("#off-actions").replaceChildren(acts);
    }
    bindRows($("#off-list"), () => list);
    if (!NET.down && rows.length)                               // fresh labels without downloading the music again
      OFF.refreshMeta(true).catch(() => {});
  },

  async playlists() {
    setTop("Плейлисты", false);
    view.innerHTML = `<div class="list" id="pl"><div class="loader">Загрузка…</div></div>`;
    const { items } = await api("/api/playlists");
    store.set("wp_pl_list", items.map(p => ({ id: p.id, name: p.name, smart: p.smart, total: p.n })));
    $("#pl").innerHTML = items.map(p => `<button class="row" data-p="${p.id}"><span class="cover sm" style="display:grid;place-items:center">${icon(p.smart ? "wave" : "list")}</span>
      <span class="row-text"><b>${esc(p.name)}</b><small>${p.smart ? "Умный плейлист" : "Плейлист"}</small></span><span class="n">${p.n}</span></button>`).join("")
      || `<div class="empty-note">Плейлистов пока нет — их можно создать в Walle Player на компьютере</div>`;
    $("#pl").onclick = e => { const b = e.target.closest("[data-p]"); if (b) go("playlist/" + b.dataset.p); };
  },

  async playlist(id) {
    setTop("Плейлист", true);
    view.innerHTML = `<div class="loader">Загрузка…</div>`;
    const r = await api("/api/playlists/" + id);
    store.set("wp_pl_" + id, { name: r.name, ids: r.items.map(t => t.id) });   // its saved tracks play without a connection
    setTop(r.name, true);
    view.innerHTML = `<div id="p-actions"></div><div class="list" id="p-list">${trackRows(r.items)}</div>${r.items.length ? "" : `<div class="empty-note">Пусто</div>`}`;
    $("#p-actions").replaceChildren(listActions(() => r.items));
    bindRows($("#p-list"), () => r.items);
  },
};

// does a saved track fit the wave? the same rule as the server: a confirmed mood, or the model's guess >= 55%; any genre
function waveFits(t, w) {
  const want = new Set((w?.moods || []).map(m => m.toLowerCase()));
  const moods = [...(t.moods || []), ...(t.suggested || []).filter(s => (s.conf || 0) >= 0.55).map(s => s.mood)]
    .filter(m => want.has(m.toLowerCase()));
  const genres = new Set((w?.genres || []).map(g => g.toLowerCase()));
  if (want.size && !moods.length) return null;
  if (genres.size && !(t.genres || []).some(g => genres.has(g.toLowerCase()))) return null;
  return [...new Set(moods)];
}
async function offlineTracks(n, exclude = new Set(), wave = null) {
  let all = (await OFF.list()).map(r => r.track);
  if (wave) all = all.flatMap(t => { const m = waveFits(t, wave); return m ? [{ ...t, wave: { kind: "known", moods: m, conf: null } }] : []; });
  const pool = all.filter(t => !exclude.has(t.id));
  const src = pool.length ? pool : all;
  for (let k = src.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [src[k], src[j]] = [src[j], src[k]]; }
  return src.slice(0, n);
}
async function playOffline(shuffle) {
  const list = await offlineTracks(500);
  if (!list.length) return toast("На устройстве пока ничего не скачано");
  P.shuffle = !!shuffle;
  P.play(list, 0);
}

const sameSet = (a = [], b = []) => a.length === b.length && a.every(x => b.includes(x));
const sameWave = (a, b) => a && b && a.character === b.character && sameSet(a.moods, b.moods) && sameSet(a.genres, b.genres);
const waveTitle = w => [w.moods.join(" · "), (w.genres || []).join(" · ")].filter(Boolean).join(" — ") || "Все настроения";

async function startWave(ws) {
  try {
    const params = { moods: ws.moods.slice(), character: ws.character, genres: (ws.genres || []).slice() };
    const res = await api("/api/wave/next", { ...params, exclude: [], n: 10 });
    if (!res.items.length) return toast("Под такой выбор пока нет треков — выберите другие настроения или жанры");
    P.play(res.items, 0, { wave: params });
  } catch (e) {
    if (e.message === NOCONN && OFF.ids.size) {             // no connection: the wave from the music saved on the device
      const params = { moods: ws.moods.slice(), character: ws.character, genres: (ws.genres || []).slice() };
      const list = await offlineTracks(10, new Set(), params);
      if (!list.length) return toast(`Нет связи, а среди скачанного нет треков: ${waveTitle(params)}`, 4000);
      P.play(list, 0, { wave: params });
      toast("Нет связи — волна из скачанного");
    } else if (e.message !== "401") toast("Волна: " + e.message);
  }
}

function renderWaveNow() {
  const box = $("#wave-now");
  if (!box) return;
  const t = P.cur;
  box.hidden = !(t && P.wave);
  if (!t || !P.wave) return;
  const g = isGuess(t);
  box.innerHTML = `${coverEl(t, "md")}<span class="row-text"><b>${esc(t.title)}</b><small>${esc(t.artist)}${g ? ` · модель думает: ${esc(t.wave.moods.join(", "))}` : ""}</small></span>
    ${g ? `<button class="icon-btn" data-rate="dislike" aria-label="Не подходит">${icon("thumbdown")}</button><button class="icon-btn" data-rate="like" aria-label="Подходит">${icon("up")}</button>`
        : `<button class="icon-btn" data-act="next" aria-label="Следующий">${icon("next")}</button>`}`;
  box.onclick = e => { if (!e.target.closest("button")) openFull(); };
}

function openProfile() {
  const u = INFO.user || {};
  openSheet(u.name || "Слушатель", `<form class="genre-add" id="rename" style="margin:0 0 12px"><input id="rename-name" value="${esc(u.name || "")}" maxlength="40" placeholder="Ваше имя" autocomplete="off"><button class="pill" type="submit">Переименовать</button></form>
    <p style="color:var(--muted);margin:0 0 12px">${u.owner ? "Владелец библиотеки: лайки — это «Избранное» на компьютере, прослушивания идут в общую статистику."
      : "Ваши лайки и история прослушиваний — только ваши, они не смешиваются с владельцем библиотеки."}
      ${u.can_label ? "Вы можете размечать настроения и жанры." : ""}</p>
    <p style="color:var(--dim);font-size:13px">${esc(INFO.name || "")} · ${INFO.tracks} треков · Walle Player ${esc(INFO.version)}</p>
    ${OFF.available ? `<button class="menu-item" id="prof-off">${icon("download")}<span id="prof-off-text">Скачанное на устройстве</span></button>` : ""}
    <div class="actions"><button class="pill ghost" id="logout">Выйти на этом устройстве</button></div>`, body => {
    $("#rename", body).onsubmit = async e => {
      e.preventDefault();
      const name = $("#rename-name", body).value.trim();
      if (!name || name === u.name) return;
      try {
        INFO.user = await api("/api/me", { name });
        $("#sheet-title").textContent = INFO.user.name;
        $("#side-foot").textContent = `${INFO.user.name} · ${INFO.tracks} треков`;
        P.save(); toast("Теперь вы — " + INFO.user.name);
      } catch (err) { if (err.message !== "401") toast("Ошибка: " + err.message); }
    };
    const po = $("#prof-off", body);
    if (po) {
      po.onclick = () => { closeSheet(); go("offline"); };
      OFF.usage().then(u => { $("#prof-off-text", body).textContent = `Скачанное на устройстве: ${u.count} трек. · ${u.count ? fmtSize(u.bytes) : "0 МБ"}`; });
    }
    $("#logout", body).onclick = async () => { await fetch("/api/logout", { method: "POST" }); store.set("wp_state", null); location.reload(); };
  });
}
$("#top-mark").onclick = openProfile;
$("#side-foot").onclick = openProfile;

// ---- updates: the computer got a newer Walle Player while this page stayed open --------------------------------------
async function checkUpdate() {
  if (!LOADED_VERSION) return;
  try {
    const p = await (await fetch("/api/ping", { cache: "no-store" })).json();
    if (p.version && p.version !== LOADED_VERSION) { $("#upd-ver").textContent = p.version; $("#upd").hidden = false; }
  } catch { /* no connection: the banner about it is shown elsewhere */ }
}
$("#upd-go").onclick = () => { P.save(); location.reload(); };
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkUpdate(); });
setInterval(checkUpdate, 10 * 60e3);

// ---- login / start ---------------------------------------------------------------------------------------------------
// ---- login: a key (QR link) or an access request the owner approves on the computer ---------------------------------
async function showLogin() {
  if (!$("#login").hidden) return;
  $("#login").hidden = false;
  const srv = $("#login-server");
  try {                                                         // the connection is checked at once
    const p = await (await fetch("/api/ping", { cache: "no-store" })).json();
    srv.textContent = `✓ Связь с компьютером есть${p.name ? ` — «${p.name}»` : ""}`;
    srv.className = "login-server ok";
  } catch {
    srv.textContent = "Нет связи с компьютером: Walle Player выключен или «Другие устройства» не открыты";
    srv.className = "login-server bad";
  }
  const pending = store.get("wp_join", null);
  if (pending) waitJoin(pending);
}
let joinTimer = null;
function waitJoin(req) {
  $("#join-box").hidden = true; $("#join-wait").hidden = false; $("#login-err").textContent = "";
  clearInterval(joinTimer);
  joinTimer = setInterval(async () => {
    let st;
    try { const r = await fetch(`/api/join/${req.id}?s=${encodeURIComponent(req.secret)}`, { cache: "no-store" }); st = r.ok ? await r.json() : { status: "gone" }; }
    catch { return; }                                           // no connection for a moment: keep waiting
    if (st.status === "pending") return;
    clearInterval(joinTimer); store.set("wp_join", null);
    $("#join-box").hidden = false; $("#join-wait").hidden = true;
    if (st.status === "approved") { $("#login").hidden = true; toast(`Добро пожаловать, ${st.user}!`); start(); }
    else $("#login-err").textContent = st.status === "denied" ? "Хозяин библиотеки отклонил запрос" : "Запрос устарел — попросите ещё раз";
  }, 2000);
}
$("#join-form").addEventListener("submit", async e => {
  e.preventDefault();
  const name = $("#join-name").value.trim();
  if (!name) return $("#join-name").focus();
  try {
    const r = await fetch("/api/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    const j = await r.json();
    if (!r.ok) return void ($("#login-err").textContent = r.status === 403 ? "Хозяин выключил запросы доступа — попросите у него QR-код"
                                                       : r.status === 429 ? "Слишком много запросов, попробуйте позже" : j.error);
    const req = { id: j.id, secret: j.secret };
    store.set("wp_join", req);
    waitJoin(req);
  } catch { $("#login-err").textContent = "Нет связи с компьютером"; }
});
$("#join-cancel").onclick = () => {
  clearInterval(joinTimer); store.set("wp_join", null);
  $("#join-box").hidden = false; $("#join-wait").hidden = true;
};
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const key = $("#login-key").value.trim();
  if (!key) return;
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) });
  if (r.ok) { $("#login").hidden = true; clearInterval(joinTimer); store.set("wp_join", null); start(); }
  else $("#login-err").textContent = r.status === 429 ? "Слишком много попыток, подождите 10 минут" : "Ключ не подходит";
});

document.addEventListener("keydown", e => {
  if (e.target.matches("input, select, textarea")) return;
  if (e.code === "Space") { e.preventDefault(); P.toggle(); }
  else if (e.code === "ArrowRight" && e.ctrlKey) P.next();
  else if (e.code === "ArrowLeft" && e.ctrlKey) P.prev();
  else if (e.code === "ArrowRight") audio.currentTime = Math.min((audio.duration || 0) - 0.5, audio.currentTime + 10);
  else if (e.code === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 10);
  else if (e.code === "KeyF") toggleLike(P.cur);
  else if (e.code === "Escape") { closeSheet(); closeFull(); }
});

async function start() {
  await OFF.init();
  let offline = false;
  try { INFO = await api("/api/info"); store.set("wp_info", INFO); PENDING.flush(); OFF.refreshMeta().catch(() => {}); }
  catch (e) {
    const cached = store.get("wp_info", null);
    if (e.message !== NOCONN || !cached) return;
    INFO = cached; offline = true;                              // opened without a connection: the saved music
  }
  for (const m of INFO.moods) MOOD[m.name] = m;
  if (!offline) LOADED_VERSION = INFO.version;
  $("#side-foot").textContent = `${INFO.user?.name || ""} · ${INFO.tracks} треков`;
  renderTabs();
  if (offline) netDown(); else render();               // without a connection every page shows the saved music
  if (!P.queue.length) P.restore();
}
start();
