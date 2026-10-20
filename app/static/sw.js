// VERDET service worker: makes the app installable and shows a friendly page offline.
// Pages and data always come from the network (they're live); only the offline page
// and the icons are cached.
const CACHE = "verdet-v1";
const PRECACHE = ["/offline", "/static/manifest.json", "/static/icon-192.png", "/static/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.mode === "navigate") {
    // Network first; if the phone is offline, show the cached offline page.
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  if (req.url.includes("/static/")) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  }
});
