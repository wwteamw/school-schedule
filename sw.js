const CACHE_NAME = 'shrk-schedule-v1';
const CORE_ASSETS = [
  './',
  './index.html',
  './icon.svg?v=1',
  './manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Стратегия Network-First: при наличии интернета всегда берём свежее расписание,
// а если в школе пропала сеть — мгновенно отдаём последнюю сохранённую копию
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  const isScheduleJson = url.pathname.endsWith('/schedule.json');
  const cacheKey = isScheduleJson ? new Request('./schedule.json') : event.request;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(cacheKey, copy)).catch(() => {});
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(cacheKey);
        if (cached) return cached;
        if (event.request.mode === 'navigate') {
          return caches.match('./index.html') || caches.match('./');
        }
        throw new Error('Offline and not in cache');
      })
  );
});
