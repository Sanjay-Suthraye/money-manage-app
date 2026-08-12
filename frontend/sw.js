/* Conservative service worker: never touches /api; caches the static shell.
   - /vendor/*: stale-while-revalidate (big, effectively immutable)
   - navigations: network-first, offline falls back to the cached shell
   - /js /css /manifest /icons: network-first, cache successful responses
   Bump CACHE to purge everything. */

const CACHE = "money-v1";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function staleWhileRevalidate(req) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(req);
  const network = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => cached);
  return cached || network;
}

async function networkFirst(req, fallbackKey) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(fallbackKey || req, res.clone());
    return res;
  } catch (err) {
    const cached = await cache.match(fallbackKey || req);
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api")) return; // data + auth: never cached

  if (url.pathname.startsWith("/vendor/")) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/"));
    return;
  }
  if (/^\/(js|css)\//.test(url.pathname) || url.pathname === "/manifest.json"
      || /^\/icon-.*\.png$/.test(url.pathname)) {
    event.respondWith(networkFirst(request));
  }
});
