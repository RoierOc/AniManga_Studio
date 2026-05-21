const CACHE = 'manga-upscaler-v11';
const SHELL = ['/', '/static/css/styles.css', '/static/js/app.js', '/static/js/vue.js', '/static/favicon.svg'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ));
    self.clients.claim();
});

self.addEventListener('fetch', e => {
    const url = new URL(e.request.url);
    // API + uploads → always network
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/uploads/')) return;
    // HTML root → always network so changes appear on normal F5
    if (url.pathname === '/' || url.pathname.endsWith('.html')) return;
    // Static assets (JS/CSS/fonts) → cache-first
    e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
