/* AVIO - service worker minimale.
   Strategia: rete per prima (il codice cambia spesso), cache come rete di
   sicurezza quando sei offline. I dati (/api) non vengono mai messi in cache.

   I path sono relativi alla base dello SW, cosi' funziona sia in locale (/)
   sia su GitHub Pages (/avio/). */
const BASE = new URL("./", self.location).pathname;
const CACHE = "avio-v12";
const SHELL = [
  BASE,
  "tokens.css",
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
    // Rete per prima, e SENZA cache HTTP: GitHub Pages tiene i file per 10
    // minuti, e un app.js vecchio contiene il vecchio indirizzo del backend
    // (il tunnel cambia URL a ogni riavvio) -> "Failed to fetch" al login.
    fetch(request, { cache: "no-store" })
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
    data = { title: "AVIO", body: event.data ? event.data.text() : "Nuova offerta" };
  }
  const title = data.title || "AVIO";
  const body = (data.body || "Ci sono novita' per una tua ricerca.").slice(0, 400);
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: BASE + "icons/icon-192.png",
      badge: BASE + "icons/icon-192.png",
      data: { url: data.url || "" },
      tag: data.tag || "avio",
    })
  );
});

/* Il tocco apre la ricerca giusta. L'URL arriva relativo alla pagina
   (`?watch=12`): va risolto sulla base del service worker, non sulla radice
   del sito, altrimenti su GitHub Pages si finirebbe fuori da /avio/. */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const relativo = (event.notification.data && event.notification.data.url) || "";
  let target = self.registration.scope;
  try {
    target = new URL(relativo, self.registration.scope).href;
  } catch (_) { /* resta la pagina dell'app */ }
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      const client = list.find((c) => c.url.includes(self.location.origin));
      if (client) {
        return client.focus().then(() => {
          if (target !== self.registration.scope && "navigate" in client) {
            return client.navigate(target);
          }
          return undefined;
        });
      }
      return self.clients.openWindow(target);
    })
  );
});
