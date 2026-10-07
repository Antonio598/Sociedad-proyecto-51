/* TRAYECTO — Service worker de la app del operador.
 *
 * Hace que la app ABRA sin señal: guarda una copia del "cascarón" (el HTML de la app y
 * sus recursos) la primera vez que hay red, y después la sirve desde el teléfono cuando
 * no la hay. Los datos (/api/) nunca se cachean: sin red fallan a propósito y la app los
 * maneja con la cola local. Con esto la cola offline tiene dónde correr.
 */
const CACHE = 'trayecto-v2';
const SHELL = '/';   // el cascarón del operador se sirve en la raíz

self.addEventListener('install', (e) => {
  // Toma el control cuanto antes; el cascarón real se cachea en la primera navegación.
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    // Limpia versiones viejas del caché.
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  // Solo GET: POST/PUT (crear solicitud, subir foto) nunca se tocan ni se cachean.
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  // Datos del servidor: red directa; sin red fallan y la app usa la cola/caché local.
  if (url.pathname.startsWith('/api/')) return;

  // Navegaciones (abrir la app): red primero, y si no hay, el cascarón guardado.
  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      try {
        const res = await fetch(req);
        // Solo se guarda el cascarón REAL de la app: la raíz, con 200 y sin redirección.
        // Así una respuesta de /login o un 303 (sesión vencida) no envenena el cache offline.
        if (res && res.ok && !res.redirected && url.pathname === SHELL) {
          const cache = await caches.open(CACHE);
          cache.put(SHELL, res.clone());
        }
        return res;
      } catch (_) {
        const cache = await caches.open(CACHE);
        return (await cache.match(SHELL)) || Response.error();
      }
    })());
    return;
  }

  // Recursos (fuentes, estilos, scripts, iconos): sirve del caché y refresca en segundo
  // plano (stale-while-revalidate). Así cargan al instante y quedan disponibles offline.
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req);
    const fresh = fetch(req).then((res) => {
      if (res && (res.ok || res.type === 'opaque')) cache.put(req, res.clone());
      return res;
    }).catch(() => null);
    return cached || (await fresh) || Response.error();
  })());
});
