/* Walle Player service worker: keeps the player itself (page, scripts, styles, icons) and the covers, so the player
   opens without a connection and plays the music saved on the device.  Works only over https / localhost.
   The audio is not touched here: saved tracks live in IndexedDB (offline.js), the rest streams with Range. */
const SHELL = "walle-shell-v1", COVERS = "walle-covers-v1";
const SHELL_FILES = ["/", "/static/app.js", "/static/offline.js", "/static/app.css", "/manifest.webmanifest",
  "/static/mark.png", "/static/wordmark.png", "/static/icon-256.png", "/static/favicon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![SHELL, COVERS].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/cover/")) {               // covers never change: cache first
    e.respondWith(caches.open(COVERS).then(async c => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const r = await fetch(e.request);
      if (r.ok) c.put(e.request, r.clone());
      return r;
    }));
    return;
  }
  const shell = e.request.mode === "navigate" || url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest";
  if (!shell) return;                                           // the API and the audio go to the network as usual
  const key = e.request.mode === "navigate" ? "/" : url.pathname;
  e.respondWith(fetch(e.request).then(r => {                     // network first: an update is picked up at once
    if (r.ok && !r.redirected) { const copy = r.clone(); caches.open(SHELL).then(c => c.put(key, copy)); }
    return r;
  }).catch(() => caches.match(key)));
});
