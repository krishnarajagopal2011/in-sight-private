// in_sight service worker — makes the phone PWA installable + resilient.
// Strategy: cache the app shell so it launches instantly and survives a dropped
// connection (showing the last view); page navigations go to the network so
// redirects (the login gate, etc.) resolve natively; the JSON API is network-only.
const CACHE = "insight-v18";
const SHELL = [
  "/life",
  "/static/css/kiosk.css",
  "/static/js/common.js", "/static/js/life.js",
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
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);

  // API: network-only (the page keeps the last good data if it fails).
  if (url.pathname.startsWith("/api/")) return;

  // Page navigations: ALWAYS go to the network so redirects (e.g. the login gate)
  // resolve natively. Never return a redirected response here — that breaks the
  // navigation with ERR_FAILED. Fall back to the cached shell only when offline.
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/life")));
    return;
  }

  // Static assets: cache-first, and refresh the cache in the background.
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
