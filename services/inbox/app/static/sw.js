// Noteezer's service worker. It makes the app installable and keeps the look
// working on a bad connection. Notes themselves always come from the server:
// pages are fetched from the network first, and only the shell is cached.
const CACHE = "noteezer-shell-v1";
const SHELL = [
  "/static/app.css",
  "/static/app.js",
  "/static/offline.html",
  "/static/icons/icon-192.png",
  "/static/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  // Caching the shell is a bonus. If the browser's cache storage fails, the
  // worker still installs, so the app stays installable.
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => {}),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return; // saves and shares always go to the server

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // A page: try the server, and show the offline page if it cannot be reached.
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    // Styles, script, and icons: use the cached copy, and refresh it in the background.
    event.respondWith(
      caches.match(request).then((cached) => {
        const fresh = fetch(request)
          .then((response) => {
            if (response.ok) caches.open(CACHE).then((cache) => cache.put(request, response.clone()));
            return response;
          })
          .catch(() => cached);
        return cached || fresh;
      }),
    );
  }
  // Anything else, like saved images, is private and never cached.
});
