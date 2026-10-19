/* Carpool Hunter - service worker minimale.
   Strategia: rete per prima (il codice cambia spesso), cache come rete di
   sicurezza quando sei offline. I dati (/api) non vengono mai messi in cache. */
const CACHE = "carpool-v2";
const SHELL = [
  "/",
  "/static/styles.css",
  "/static/app.js",
  "/static/favicon.svg",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL).catch(() => undefined))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  // Mai in cache: API, configurazione e origini esterne (tunnel/Pages).
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/config.json") return;

  event.respondWith(
    // Rete per prima: se il PC e' raggiungibile vedi sempre l'ultima versione.
    fetch(request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
  );
});
