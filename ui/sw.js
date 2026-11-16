// Noteezer's service worker. It makes the app installable, keeps the shell
// working on a bad connection, and receives Android's share sheet. Notes
// always come from the services: their calls go to other origins, which this
// worker never touches or caches.
const CACHE = "noteezer-shell-v2";
const SHARE_CACHE = "noteezer-share";
const SHELL = ["/", "/index.html", "/app.css", "/app.js", "/config.js", "/offline.html", "/icons/icon-192.png", "/icons/favicon-32.png"];

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
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE && key !== SHARE_CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.method === "POST" && url.pathname === "/share") {
    // The share sheet posts here, but this site is static and the token lives
    // in the page. Keep the post and open the app, which sends it to inbox.
    event.respondWith(
      request
        .formData()
        .then((data) => caches.open(SHARE_CACHE).then((cache) => cache.put("/shared", new Response(data))))
        .catch(() => {})
        .then(() => Response.redirect("/?shared=1", 303)),
    );
    return;
  }
  if (request.method !== "GET") return;

  if (request.mode === "navigate") {
    // The page: try the network, then the cached shell, then the offline page.
    event.respondWith(
      fetch(request).catch(() => caches.match("/index.html").then((cached) => cached || caches.match("/offline.html"))),
    );
    return;
  }

  // Styles, script, config, and icons: use the cached copy, and refresh it in the background.
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
});
