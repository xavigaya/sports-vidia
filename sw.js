// Service worker de Sports VidIA Vòlei
const VERSION = 'vidia-v8';
const SHELL = ['./', 'index.html', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/icon-maskable-512.png',
  'icons/apple-touch-icon.png', 'icons/favicon-32.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Pàgina: primer la xarxa (per rebre actualitzacions), si no hi ha connexió, la còpia desada
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).then(r => {
      const copy = r.clone(); caches.open(VERSION).then(c => c.put('index.html', copy)); return r;
    }).catch(() => caches.match('index.html')));
    return;
  }
  // Fitxers propis: còpia desada primer
  if (url.origin === location.origin) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(r => {
      const copy = r.clone(); caches.open(VERSION).then(c => c.put(req, copy)); return r;
    })));
    return;
  }
  // Tipografies de Google: desa-les per poder treballar sense connexió
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(caches.match(req).then(hit => {
      const net = fetch(req).then(r => { const copy = r.clone(); caches.open(VERSION).then(c => c.put(req, copy)); return r; }).catch(() => hit);
      return hit || net;
    }));
  }
  // YouTube i la resta: sempre per xarxa
});
