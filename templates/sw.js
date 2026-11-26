const CACHE = '21party-v2';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
  
  e.respondWith(
    caches.open(CACHE).then(cache => {
      return cache.match(e.request).then(match => {
        return match || fetch(e.request).then(response => {
          try { cache.put(e.request, response.clone()); } catch(_) {}
          return response;
        }).catch(() => cache.match('/'));
      });
    })
  );
});
