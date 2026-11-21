// efeTrack çevrimdışı görüntüleme (service worker).
// Açtığın sayfaların bir kopyasını saklar; internet yokken o kopyayı "çevrimdışı" şeridiyle gösterir.
// İnternet varken her zaman sunucudaki güncel sayfa gelir (önce ağ, olmazsa kopya).
const VERSION = "{{ version }}";
const PAGES = "efe-sayfalar-" + VERSION;
const STATIC = "efe-statik-" + VERSION;
const IMAGES = "efe-resimler-2";
const KEEP = [PAGES, STATIC, IMAGES];
const PRECACHE = {{ precache|safe }};
const NETWORK_WAIT_MS = 4000;
const MAX_IMAGES = 600;
// Kişisel ayarlar (bildirim adresleri, entegrasyonlar), giriş ve sistem sayfaları hiç saklanmaz.
const SKIP = ["/accounts/", "/admin", "/sw.js", "/cdn-cgi/", "/poster-duvari/afis", "/afis-indir", "/settings", "/durum"];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(PAGES).then((cache) =>
      Promise.all(PRECACHE.map((url) =>
        fetch(url, { credentials: "same-origin" }).then((r) => (cacheable(r) ? cache.put(url, r) : null)).catch(() => null)
      ))
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("efe-") && !KEEP.includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function cacheable(response) {
  return response && response.ok && response.status === 200 && response.type === "basic" && !response.redirected &&
    (response.headers.get("content-type") || "").includes("text/html");
}

function pageKey(request, url) {
  // htmx parçaları aynı adreste farklı içerik döndürür: ayrı anahtar.
  if (request.headers.get("HX-Request")) {
    const key = new URL(url); key.searchParams.set("__hx", "1"); return key.toString();
  }
  return url.toString();
}

function offlineBanner(html, saved) {
  const when = saved ? new Date(saved).toLocaleString("tr-TR", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "";
  const bar = '<div style="position:sticky;top:0;z-index:9999;background:#b45309;color:#fff;' +
    'font:600 13px -apple-system,BlinkMacSystemFont,sans-serif;padding:8px 12px;text-align:center">' +
    "📴 Çevrimdışı · kayıtlı kopya" + (when ? " (" + when + ")" : "") + " · değişiklik yapılamaz</div>";
  return html.replace(/<body([^>]*)>/i, "<body$1>" + bar);
}

async function offlineList() {
  const cache = await caches.open(PAGES);
  const items = [];
  for (const request of (await cache.keys()).slice(0, 60)) {
    const url = new URL(request.url);
    if (url.searchParams.has("__hx")) continue;
    const response = await cache.match(request);
    const title = ((await response.text()).match(/<title>\s*([^<]+?)\s*<\/title>/i) || [])[1] || url.pathname;
    items.push('<li><a href="' + url.pathname + url.search + '">' + title.replace(/</g, "&lt;") + "</a></li>");
  }
  const html = '<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    "<title>Çevrimdışı - efeTrack</title><style>body{margin:0;background:#0E1116;color:#e5e7eb;font:15px -apple-system,BlinkMacSystemFont,sans-serif;padding:24px}" +
    "h1{font-size:22px}a{color:#a5b4fc;text-decoration:none}li{margin:10px 0}p{color:#9aa3b5}</style></head><body>" +
    "<h1>📴 İnternet yok</h1><p>Bu sayfanın kayıtlı bir kopyası yok. Daha önce açtığın şu sayfalar internetsiz de açılır:</p><ul>" +
    (items.join("") || "<li>Henüz kayıtlı sayfa yok.</li>") + "</ul></body></html>";
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function networkFirst(event, request, url) {
  const key = pageKey(request, url);
  const network = fetch(request);
  // Kopyayı güncelleme işi en baştan kaydedilir: ağ geç gelse de (kopya gösterildikten sonra) yazılır.
  event.waitUntil(network.then(async (response) => {
    if (cacheable(response)) await (await caches.open(PAGES)).put(key, response.clone());
  }).catch(() => null));
  const cache = await caches.open(PAGES);
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), NETWORK_WAIT_MS));
  try {
    const first = await Promise.race([network, timeout]);
    if (first) return first;
  } catch (error) { /* ağ yok: aşağıda kopyaya bak */ }
  const cached = await cache.match(key);
  if (cached) {
    if (request.mode !== "navigate") return cached;
    return new Response(offlineBanner(await cached.text(), cached.headers.get("date")), {
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }
  try { return await network; } catch (error) { /* hâlâ ağ yok */ }
  return request.mode === "navigate" ? offlineList() : new Response("", { status: 504 });
}

async function staleWhileRevalidate(event, request, name, limit) {
  const cache = await caches.open(name);
  const cached = await cache.match(request);
  const update = fetch(request).then((response) => {
    if (response && (response.ok || response.type === "opaque")) {
      // Önbelleğe yazılamasa da (kota) resim yine gösterilsin.
      event.waitUntil(cache.put(request, response.clone()).then(async () => {
        if (!limit) return;
        const keys = await cache.keys();
        for (const old of keys.slice(0, Math.max(0, keys.length - limit))) await cache.delete(old);
      }).catch(() => null));
    }
    return response;
  }).catch(() => cached || Response.error());
  if (cached) { event.waitUntil(update); return cached; }
  return (await update) || Response.error();
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin === self.location.origin && url.pathname.startsWith("/accounts/logout")) {
    // Çıkış (POST formu) → kayıtlı kişisel sayfaları sil.
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("efe-")).map((k) => caches.delete(k)))));
    return;
  }
  if (request.method !== "GET") return;
  if (url.origin === self.location.origin) {
    if (SKIP.some((prefix) => url.pathname.startsWith(prefix))) return;
    if (url.pathname.startsWith("/static/")) { event.respondWith(staleWhileRevalidate(event, request, STATIC)); return; }
    if (request.mode === "navigate" || request.headers.get("HX-Request")) { event.respondWith(networkFirst(event, request, url)); }
    return;
  }
  // Yalnız normal <img> istekleri (no-cors); poster duvarı/kart çizimi cors ister, ona opak kopya verilemez.
  if (request.destination === "image" && request.mode === "no-cors") {
    event.respondWith(staleWhileRevalidate(event, request, IMAGES, MAX_IMAGES));
  }
});
