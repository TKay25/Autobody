/* Service worker — the reason the console opens when the internet is down.
 *
 * Served from `/sw.js`, NOT from `/static/`. A worker's scope is limited to the
 * path it is served from, so a copy under /static/ could only ever control
 * /static/* — it could not touch the app shell or the API, which is the whole
 * point. `app/views/views.py::service_worker` serves this file at the root.
 *
 * What it does, and deliberately does not do:
 *
 *   navigations      network first, fall back to the cached shell. Network first
 *                    so a deploy is picked up on the next load rather than
 *                    whenever the cache happens to expire.
 *   /static/*        cache first. Every asset URL carries the file's mtime as a
 *                    query string (see `static_url()`), so an edited file is a
 *                    different URL and can never be served stale from here.
 *   /api/* GET       network first, keep a copy, fall back to the copy. The
 *                    fallback response is stamped `X-Served-From: cache` so the
 *                    UI can say "as of 14:20" instead of implying it is live.
 *   everything else  untouched. That includes every write: POST/PATCH/DELETE are
 *                    NOT intercepted, because the app has its own outbox that
 *                    queues them with idempotency keys. Handling them here too
 *                    would mean two things racing to send the same payment.
 */
const VERSION = 'v1';
const SHELL = `topclass-shell-${VERSION}`;
const DATA = `topclass-data-${VERSION}`;
const OWNED = [SHELL, DATA];

// Needed for a first paint with no network at all. The rest of the app caches
// itself as it is used, because every asset URL is mtime-versioned and cannot be
// listed ahead of time.
const PRECACHE = [
  '/app',
  '/static/css/app.css',
  '/static/js/core.js',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
];

/** Fetch with a deadline.
 *
 * `cache.add()` has no timeout, and a CDN that accepts the connection and then
 * never answers will block `install` for ever — which means the worker never
 * activates, `register()` never settles, and the app silently stays online-only.
 * That is exactly what happened the first time this worker was written. A slow
 * third party must cost us the stylesheet, never the offline capability.
 */
function fetchWithDeadline(url, ms = 6000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return fetch(url, { signal: controller.signal, cache: 'reload' })
    .finally(() => clearTimeout(timer));
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Individually, so one 404, one timeout or a cold CDN cannot fail the whole
    // install and leave the app with no worker at all.
    await Promise.all(PRECACHE.map(async (url) => {
      try {
        const response = await fetchWithDeadline(url);
        if (response.ok) await cache.put(url, response);
      } catch (err) { /* this one asset is simply unavailable offline */ }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((n) => n.startsWith('topclass-') && !OWNED.includes(n))
      .map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

/** A cached response, marked so the UI can be honest about its age. */
async function stamp(response, cachedAt) {
  const headers = new Headers(response.headers);
  headers.set('X-Served-From', 'cache');
  if (cachedAt) headers.set('X-Cached-At', new Date(cachedAt).toISOString());
  const body = await response.blob();
  return new Response(body, { status: response.status, statusText: response.statusText,
                              headers });
}

async function networkFirst(request, cacheName, { stampWhenCached = true } = {}) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    // Only cache a good, complete response. A 401 or a 500 cached here would
    // haunt the next call.
    if (response.ok && response.type !== 'opaqueredirect') {
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const hit = await cache.match(request);
    if (!hit) throw err;
    const when = Date.parse(hit.headers.get('date') || '') || undefined;
    return stampWhenCached ? stamp(hit, when) : hit;
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Writes are the app's business, never the worker's. See the header comment.
  if (request.method !== 'GET') return;

  // A different origin that is not our CDN — leave it alone.
  const ours = url.origin === self.location.origin;
  const cdn = /cdn\.jsdelivr\.net$/.test(url.hostname);
  if (!ours && !cdn) return;

  // Navigations: the shell.
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return await networkFirst(request, SHELL, { stampWhenCached: false });
      } catch (err) {
        const shell = await caches.match('/app');
        if (shell) return shell;
        throw err;
      }
    })());
    return;
  }

  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirst(request, DATA));
    return;
  }

  if (url.pathname.startsWith('/static/') || cdn) {
    event.respondWith(cacheFirst(request, SHELL).catch(() => caches.match(request)));
  }
});

/** Let the page ask for the cached API data to be thrown away.
 *
 * Called on sign-in and sign-out. On a workshop PC several people use the same
 * browser, and cached API responses contain customers, money and job cards —
 * serving the previous operator's data to the next one would be a real leak, so
 * the data cache is scoped to a session rather than to the browser. */
self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'clear-data') {
    event.waitUntil(caches.delete(DATA));
  }
  if (data.type === 'skip-waiting') self.skipWaiting();
});
