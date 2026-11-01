// ArcTime Service Worker: zeigt Push-Benachrichtigungen (Release-Erinnerungen) auch bei geschlossener Seite.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: 'ArcTime', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'ArcTime', {
    body: d.body || '',
    icon: d.icon || '/icon.png',
    badge: '/icon.png',
    tag: d.tag || undefined,
    data: { url: d.url || '/#releases' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/#releases';
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) { await c.focus(); c.postMessage({ type: 'open-releases' }); return; }
    }
    await self.clients.openWindow(url);
  })());
});
