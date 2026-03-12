const CACHE_NAME = 'judo-judge-v3';
const ASSETS = [
    './',
    './index.html',
    './style.css',
    './app.js'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(ASSETS))
    );
});

self.addEventListener('fetch', event => {
    // Network First strategy for all assets to ensure we get real-time data
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // If network works, update cache and return
                return caches.open(CACHE_NAME).then(cache => {
                    cache.put(event.request, response.clone());
                    return response;
                });
            })
            .catch(() => {
                // If network fails, try cache
                return caches.match(event.request);
            })
    );
});
