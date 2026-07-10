// Daisy service worker — makes the installed app open instantly and
// feel "always there", without ever caching stale answers from Daisy
// herself. Chat/API traffic always goes to the network; only the
// app shell (the page itself + icons) is cached for fast/offline opens.

const CACHE_NAME = 'daisy-shell-v2';
const SHELL_URLS = [
  '/',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

// Paths that must NEVER be served from cache — always live.
const NEVER_CACHE = ['/ask', '/api/', '/daisy/', '/reload', '/export/'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // Cache each file independently — if one 404s (e.g. a path that
      // changes later), it only skips that file instead of aborting
      // the whole install like cache.addAll() would. That silent
      // all-or-nothing failure is exactly what broke offline opens
      // before: one stale URL in the list meant NOTHING got cached,
      // not even the page itself.
      Promise.all(
        SHELL_URLS.map((url) =>
          cache.add(url).catch((err) => console.warn('[SW] could not cache', url, err))
        )
      )
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // never intercept POST/PUT/DELETE (chat, projects, etc.)

  const url = new URL(req.url);
  const isLive = NEVER_CACHE.some((p) => url.pathname.startsWith(p));
  if (isLive || url.origin !== self.location.origin) {
    return; // let it go straight to the network, untouched
  }

  // Navigations (opening/reloading the app itself) get their own path:
  // always fall back to the cached app shell on any failure, no matter
  // what exact URL/query-string was requested (e.g. the PWA's
  // start_url "/?source=pwa" won't exactly match a cached "/" — this
  // fallback is what makes that still work instead of showing the
  // browser's own generic offline page).
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put('/', copy));
          return res;
        })
        .catch(() => caches.match('/', { ignoreSearch: true }))
    );
    return;
  }

  // Everything else in the shell: network-first so updates land
  // immediately, falling back to the cached copy if the network is down.
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req, { ignoreSearch: true }).then((cached) => cached || caches.match('/')))
  );
});
