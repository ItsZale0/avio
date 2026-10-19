/* Carpool Hunter - service worker minimale.
   Strategia: rete per prima (il codice cambia spesso), cache come rete di
   sicurezza quando sei offline. I dati (/api) non vengono mai messi in cache.

   I path sono relativi alla base dello SW, cosi' funziona sia in locale (/)
   sia su GitHub Pages (/carpool-intelligence/). */
const BASE = new URL("./", self.location).pathname;
const CACHE = "carpool-v5";
const SHELL = [
  BASE,
  "styles.css",
  "app.js",
  "favicon.svg",
  "manifest.webmanifest",
  "icons/icon-192.png",
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
  if (url.pathname.startsWith(BASE + "api/") || url.pathname === BASE + "config.json") return;

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
      .catch(() => caches.match(request).then((cached) => cached || caches.match(BASE)))
  );
});

/* Notifiche push: l'agente manda titolo, testo e URL da aprire. */
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = { title: "Carpool Hunter", body: event.data ? event.data.text() : "Nuova offerta" };
  }
  const title = data.title || "Carpool Hunter";
  const body = (data.body || "Ci sono novita' per una tua ricerca.").slice(0, 400);
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: BASE + "icons/icon-192.png",
      badge: BASE + "icons/icon-192.png",
      data: { url: data.url || BASE },
      tag: data.tag || "carpool",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || BASE;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes(self.location.origin)) return client.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
