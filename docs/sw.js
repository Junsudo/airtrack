/* AIRTRACK service worker — cache-first 완전 오프라인 */
const VERSION = 'airtrack-v16-airac-2026-08-05';
const ASSETS = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.webmanifest',
  './vendor/maplibre-gl.js',
  './vendor/maplibre-gl.css',
  './vendor/fonts/Open Sans Semibold/0-255.pbf',
  './vendor/fonts/Open Sans Semibold/256-511.pbf',
  './data/land.geojson',
  './data/airways.geojson',
  './data/fixes.geojson',
  './data/navaids.geojson',
  './data/areas.geojson',
  './data/airports.geojson',
  './data/boundaries.geojson',
  './data/bnd_labels.geojson',
  './data/ctr.geojson',
  './data/tma.geojson',
  './data/fir.geojson',
  './data/rivers.geojson',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  // GitHub Pages가 max-age=600을 주므로 addAll을 그냥 쓰면 갱신 직후에도
  // 브라우저 HTTP 캐시의 옛 파일이 그대로 담긴다. 설치 때는 항상 원본을 받는다.
  e.waitUntil(
    caches.open(VERSION).then((c) => Promise.all(
      ASSETS.map((url) => fetch(new Request(url, { cache: 'reload' }))
        .then((res) => (res.ok ? c.put(url, res) : null)))
    )).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request, { ignoreSearch: true }).then((hit) => {
      if (hit) return hit;
      return fetch(e.request).then((res) => {
        // 성공한 same-origin 응답은 캐시에 보관 (최초 온라인 방문 시 채워짐)
        if (res.ok && new URL(e.request.url).origin === location.origin) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => {
        if (e.request.mode === 'navigate') return caches.match('./index.html');
        return Response.error();
      });
    })
  );
});
