// in_sight service worker — makes the phone PWA installable + resilient.
// Strategy: cache the app shell so it launches instantly and survives a dropped
// connection (showing the last view); always go to the network for the JSON API
// (the pages' own polling handles staleness), falling back to nothing so the
// page's offline-first logic keeps the last data on screen.
const CACHE = "insight-v13";
const SHELL = [
  "/life", "/projects",
  "/static/css/kiosk.css",
  "/static/js/common.js", "/static/js/life.js", "/static/js/projects.js",
  "/static/icon.svg", "/static/icon-192.png", "/static/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;

  // API: network-only (the page keeps the last good data if it fails).
  if (url.pathname.startsWith("/api/")) return;

  // App shell: cache-first, and refresh the cache in the background.
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const live = fetch(e.request).then((res) => {
        if (res && res.ok) caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
        return res;
      }).catch(() => cached);
      return cached || live;
    })
  );
});
