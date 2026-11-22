/* 启程 AI 离线壳。
 *
 * 只缓存前端静态资源，接口一律走网络：
 * 用户看到过期的额度、投递状态或计划进度，比看到「加载失败」更糟。
 */
const VERSION = 'v1';
const SHELL = `qicheng-shell-${VERSION}`;
const ASSETS = `qicheng-assets-${VERSION}`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(['/', '/manifest.webmanifest', '/icon-192.png']))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== ASSETS).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/static')) return;

  // 页面导航：优先网络，断网时回退到缓存的壳
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/').then((hit) => hit || Response.error())),
    );
    return;
  }

  // 静态资源：缓存优先，命中后后台不再重复下载
  event.respondWith(
    caches.match(request).then((hit) => {
      if (hit) return hit;
      return fetch(request)
        .then((response) => {
          if (response.ok && response.type === 'basic') {
            const copy = response.clone();
            caches.open(ASSETS).then((cache) => cache.put(request, copy)).catch(() => {});
          }
          return response;
        })
        .catch(() => Response.error());
    }),
  );
});
