"use strict";
const CACHE = "wp-android-{{VERSION}}";
const OFFLINE = "/offline.html";
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add(OFFLINE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("wp-android-") && key !== CACHE)
    .map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request, {cache:"no-store"}).catch(() => caches.open(CACHE).then(cache => cache.match(OFFLINE))));
  }
});
