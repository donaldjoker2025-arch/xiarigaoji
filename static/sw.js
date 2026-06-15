/* ============================================================
   Service Worker — 夏日告急 PWA
   Push notifications + smart caching strategy
   ============================================================ */

const CACHE_NAME = 'xrtj-cache-v3';
const STATIC_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.json',
];

/* ---- Install: pre-cache static shell ---- */
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

/* ---- Activate: clean old caches ---- */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

/* ---- Fetch: network-first for API + HTML, cache-first for other static ---- */
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isNavigation =
    event.request.mode === 'navigate' || url.pathname === '/';

  if (url.pathname.startsWith('/api/') || isNavigation) {
    // Network-first for API calls and the HTML shell so updates aren't masked
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok && event.request.method === 'GET') {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  } else {
    // Cache-first for static assets
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
  }
});

/* ---- Push: display notification ---- */
self.addEventListener('push', (event) => {
  let data = { title: '夏日告急', body: '电量变动提醒', url: '/' };

  if (event.data) {
    try {
      data = Object.assign(data, event.data.json());
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'><rect width='192' height='192' rx='36' fill='%230a0a0f'/><text x='96' y='130' font-size='108' text-anchor='middle' fill='%23ff6b35'>⚡</text></svg>",
    badge: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 96 96'><rect width='96' height='96' rx='18' fill='%23ff6b35'/><text x='48' y='68' font-size='54' text-anchor='middle' fill='white'>⚡</text></svg>",
    vibrate: [200, 100, 200],
    tag: 'xrtj-alert',
    renotify: true,
    data: { url: data.url || '/' },
    actions: data.charge_url
      ? [
          { action: 'charge', title: '💳 去充值' },
          { action: 'dismiss', title: '知道了' },
        ]
      : [{ action: 'open', title: '查看详情' }],
  };

  if (data.charge_url) {
    options.data.charge_url = data.charge_url;
  }

  event.waitUntil(self.registration.showNotification(data.title || '夏日告急', options));
});

/* ---- Notification Click: open app or charge URL ---- */
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  let targetUrl = '/';

  if (event.action === 'charge' && event.notification.data.charge_url) {
    targetUrl = event.notification.data.charge_url;
  } else if (event.notification.data && event.notification.data.url) {
    targetUrl = event.notification.data.url;
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      return clients.openWindow(targetUrl);
    })
  );
});
