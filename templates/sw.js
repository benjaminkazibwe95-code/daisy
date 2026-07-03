// Daisy service worker — makes the installed app open instantly and
// feel "always there", without ever caching stale answers from Daisy
// herself. Chat/API traffic always goes to the network; only the
// app shell (the page itself + icons) is cached for fast/offline opens.

const CACHE_NAME = 'daisy-shell-v1';
const SHELL_URLS = [
  '/',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

// Paths that must NEVER be served from cache — always live.
const NEVER_CACHE = ['/ask', '/api/', '/daisy/', '/reload'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
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

  // App shell: network-first so updates land immediately, falling
  // back to the cached copy if the network is down — this is what
  // makes the installed app open cleanly even on a bad connection.
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
  );
});
