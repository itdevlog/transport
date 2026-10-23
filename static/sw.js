"use strict";

// Кэш «оболочки» приложения. Прогнозы и прочие /api/* — только сеть.
const CACHE = "marshrut-v19";
const ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/vehicle-types.js",
  "/stops-group.js",
  "/manifest.webmanifest",
  "/icon-192.svg",
  "/icon-512.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Справочные и живые данные всегда берём из сети.
  if (url.pathname.indexOf("/api/") === 0) return;
  // Оболочку обновляем по сети при каждом запросе (кэш — только офлайн-запаска).
  // Раньше была стратегия cache-first с фиксированным именем кэша, из-за чего
  // правки app.js/style.css не доходили до уже установивших приложение браузеров.
  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
        return response;
      })
      .catch(() => caches.match(request))
  );
});
