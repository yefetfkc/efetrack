/*
 * efeTrack interface extras: the ⌘K command palette, the collapsible sidebar
 * and drag-to-reorder for the sidebar lists. Needs efe.js (window.efe).
 */
(function () {
  "use strict";

  const efe = window.efe;
  if (!efe) return;
  const root = document.documentElement;
  const anonymous = root.dataset.theme === "anon";

  /* ------------------------------------------------------------------ */
  /* Helpers                                                            */
  /* ------------------------------------------------------------------ */
  const ICONS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    plus: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    dice: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1"/><circle cx="15" cy="15" r="1"/><circle cx="15" cy="9" r="1"/><circle cx="9" cy="15" r="1"/>',
    palette: '<path d="M12 3a9 9 0 1 0 0 18c1 0 1.6-.8 1.6-1.6 0-.5-.2-.8-.5-1.1-.3-.3-.5-.7-.5-1.1 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5c0-4.1-4-7.6-9-7.6Z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10.5" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
    type: '<path d="M4 7V5h16v2M9 19h6M12 5v14"/>',
    drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11Z"/>',
    speaker: '<path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
    music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
    upload: '<path d="M12 15V4M7 9l5-5 5 5M5 20h14"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    sort: '<path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  };

  function svg(name) {
    return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
  }

  function swatch(color) {
    return '<span class="efe-palette-swatch" style="background:' + color + '"></span>';
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  const FOLD = { "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u" };

  /* Lower-case the Turkish way and fold letters, so "istatistik" finds "İstatistikler". */
  function fold(text) {
    return String(text || "")
      .toLocaleLowerCase("tr")
      .replace(/[çğıöşüâîû]/g, (c) => FOLD[c])
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
  }

  function score(query, text) {
    if (!query) return 1;
    const target = fold(text);
    const index = target.indexOf(query);
    if (index === 0) return 100;
    if (index > 0) return (/[\s·:/-]/.test(target[index - 1]) ? 80 : 50) - index * 0.1;
    let position = 0;
    for (const char of query) {
      position = target.indexOf(char, position);
      if (position < 0) return 0;
      position += 1;
    }
    return 15;
  }

  function go(path) {
    window.location.href = path;
  }

  /* ------------------------------------------------------------------ */
  /* Sidebar: collapse to icons                                         */
  /* ------------------------------------------------------------------ */
  const aside = document.querySelector("aside.efe-sidebar");

  function labelLinks() {
    if (!aside) return;
    aside.querySelectorAll("a, button").forEach((element) => {
      const label = element.querySelector("span:not([data-efe-unread-badge])");
      if (label && !element.title) element.title = label.textContent.trim();
    });
  }

  function sidebarCollapsed() {
    return root.dataset.sidebar === "collapsed";
  }

  function setSidebar(collapsed) {
    if (collapsed) root.dataset.sidebar = "collapsed";
    else delete root.dataset.sidebar;
    efe.prefs.set("sidebar", collapsed ? "collapsed" : "");
    efe.sound.play("switch");
    window.dispatchEvent(new CustomEvent("efe-sidebar-changed", { detail: { collapsed: collapsed } }));
  }

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-efe-sidebar-toggle]");
    if (!toggle) return;
    event.preventDefault();
    setSidebar(!sidebarCollapsed());
  });

  labelLinks();

  /* ------------------------------------------------------------------ */
  /* Sidebar: drag to reorder                                           */
  /* ------------------------------------------------------------------ */
  function keyOf(item) {
    const link = item.querySelector("a[href]");
    if (!link) return "";
    return link.dataset.efeMediaType ? "media:" + link.dataset.efeMediaType : new URL(link.href).pathname;
  }

  function savedOrder() {
    try {
      return JSON.parse(efe.prefs.get("order") || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function applyOrder() {
    const order = savedOrder();
    document.querySelectorAll("[data-efe-sortable]").forEach((list) => {
      const keys = order[list.dataset.efeSortable];
      if (!Array.isArray(keys) || !keys.length) return;
      const items = Array.from(list.children);
      items
        .map((item, index) => ({ item: item, index: index, rank: keys.indexOf(keyOf(item)) }))
        .sort((a, b) => {
          const ra = a.rank === -1 ? 1000 + a.index : a.rank;
          const rb = b.rank === -1 ? 1000 + b.index : b.rank;
          return ra - rb;
        })
        .forEach((entry) => list.appendChild(entry.item));
    });
  }

  function saveOrder(list) {
    const order = savedOrder();
    order[list.dataset.efeSortable] = Array.from(list.children).map(keyOf).filter(Boolean);
    efe.prefs.set("order", JSON.stringify(order));
  }

  function resetOrder() {
    efe.prefs.set("order", "");
    document.querySelectorAll("[data-efe-sortable]").forEach((list) => {
      Array.from(list.children)
        .sort((a, b) => Number(a.dataset.efeIndex) - Number(b.dataset.efeIndex))
        .forEach((item) => list.appendChild(item));
    });
    efe.sound.play("remove");
    reportSidebar();
  }

  /* Hidden sidebar items (right click → "Kenar çubuğundan gizle") */
  function hiddenKeys() {
    try {
      const keys = JSON.parse(efe.prefs.get("hidden") || "[]");
      return Array.isArray(keys) ? keys : [];
    } catch (e) {
      return [];
    }
  }

  function applyHidden() {
    const keys = hiddenKeys();
    document.querySelectorAll("[data-efe-sortable] > li").forEach((item) => {
      item.hidden = keys.indexOf(keyOf(item)) !== -1;
    });
  }

  function setHidden(key, hide) {
    const keys = hiddenKeys().filter((other) => other !== key);
    if (hide) keys.push(key);
    efe.prefs.set("hidden", keys.length ? JSON.stringify(keys) : "");
    applyHidden();
    efe.sound.play(hide ? "remove" : "success");
    reportSidebar();
  }

  function showAllHidden() {
    efe.prefs.set("hidden", "");
    applyHidden();
    efe.sound.play("success");
    reportSidebar();
  }

  function itemLabel(item) {
    const link = item.querySelector("a[href]");
    const span = link && link.querySelector("span:not([data-efe-unread-badge])");
    return (span || link || item).textContent.trim();
  }

  function hiddenItems() {
    const keys = hiddenKeys();
    return Array.from(document.querySelectorAll("[data-efe-sortable] > li"))
      .filter((item) => keys.indexOf(keyOf(item)) !== -1)
      .map((item) => ({ key: keyOf(item), label: itemLabel(item) }));
  }

  /* The macOS app maps ⌘1…⌘9 to the visible sidebar items, in this order. */
  function reportSidebar() {
    let handler = null;
    try {
      handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.efe;
    } catch (e) {
      handler = null;
    }
    if (!handler || anonymous) return;
    const items = Array.from(document.querySelectorAll("[data-efe-sortable] > li"))
      .filter((item) => !item.hidden)
      .map((item) => {
        const link = item.querySelector("a[href]");
        return link ? { title: itemLabel(item), path: new URL(link.href).pathname + new URL(link.href).search } : null;
      })
      .filter(Boolean)
      .slice(0, 9);
    try {
      handler.postMessage({ type: "sidebar", items: items });
    } catch (e) {
      /* app bridge unavailable */
    }
  }

  document.querySelectorAll("[data-efe-sortable]").forEach((list) => {
    Array.from(list.children).forEach((item, index) => {
      item.dataset.efeIndex = String(index);
    });
  });
  applyOrder();
  applyHidden();
  reportSidebar();

  let drag = null;

  document.addEventListener("dragstart", (event) => {
    if (event.target.closest && event.target.closest("[data-efe-sortable]")) event.preventDefault();
  });

  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.pointerType === "touch") return;
    const item = event.target.closest("[data-efe-sortable] > li");
    if (!item) return;
    drag = { item: item, list: item.parentElement, startY: event.clientY, active: false, id: event.pointerId };
  });

  document.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const dy = event.clientY - drag.startY;
    if (!drag.active) {
      if (Math.abs(dy) < 6) return;
      drag.active = true;
      drag.item.classList.add("efe-dragging");
      drag.list.classList.add("efe-sorting");
      efe.sound.play("tap");
    }
    event.preventDefault();
    const siblings = Array.from(drag.list.children).filter((child) => child !== drag.item);
    const before = siblings.find((child) => {
      const rect = child.getBoundingClientRect();
      return event.clientY < rect.top + rect.height / 2;
    });
    const next = drag.item.nextElementSibling;
    if (before !== next && before !== drag.item) {
      drag.list.insertBefore(drag.item, before || null);
      efe.sound.play("tick");
    }
    drag.item.style.transform = "";
    const rect = drag.item.getBoundingClientRect();
    drag.item.style.transform = "translateY(" + (event.clientY - (rect.top + rect.height / 2)) + "px) scale(1.02)";
  });

  function endDrag() {
    if (!drag) return;
    const wasActive = drag.active;
    const { item, list } = drag;
    drag = null;
    if (!wasActive) return;
    item.classList.remove("efe-dragging");
    item.style.transform = "";
    list.classList.remove("efe-sorting");
    saveOrder(list);
    reportSidebar();
    efe.sound.play("success");
    // The click that ends a drag must not open the link.
    const swallow = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    document.addEventListener("click", swallow, { capture: true, once: true });
    window.setTimeout(() => document.removeEventListener("click", swallow, true), 300);
  }

  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);

  /* ------------------------------------------------------------------ */
  /* Command palette (⌘K / Ctrl+K)                                      */
  /* ------------------------------------------------------------------ */
  const THEMES = [
    ["system", "Sistem", "linear-gradient(110deg,#212529 50%,#f3f4f7 50%)"],
    ["light", "Aydınlık", "#f3f4f7"],
    ["dark", "Karanlık", "#212529"],
    ["dark-red", "Kor", "#f43f5e"],
    ["dark-purple", "Ametist", "#a855f7"],
    ["dark-blue", "Derin Deniz", "#3b82f6"],
    ["light-red", "Gül", "#e11d48"],
    ["light-purple", "Lavanta", "#9333ea"],
    ["light-blue", "Gökyüzü", "#2563eb"],
  ];
  const ACCENTS = [
    ["auto", "Temaya göre", "conic-gradient(#6366f1,#ec4899,#f59e0b,#10b981,#3b82f6,#6366f1)"],
    ["indigo", "Çivit", "#6366f1"], ["blue", "Mavi", "#3b82f6"], ["purple", "Mor", "#a855f7"],
    ["pink", "Pembe", "#ec4899"], ["red", "Kırmızı", "#f43f5e"], ["orange", "Turuncu", "#f97316"],
    ["amber", "Kehribar", "#f59e0b"], ["green", "Yeşil", "#10b981"], ["teal", "Turkuaz", "#14b8a6"],
    ["graphite", "Grafit", "#6b7280"],
  ];
  const FONTS = [
    ["system", "Sistem (SF Pro)"], ["inter", "Inter"], ["jakarta", "Plus Jakarta Sans"],
    ["manrope", "Manrope"], ["nunito", "Nunito"], ["figtree", "Figtree"],
  ];
  const PACKS = [["soft", "Yumuşak"], ["mechanical", "Mekanik"], ["retro", "Retro"]];
  const SETTINGS = [
    ["/settings/account", "Hesap ayarları"], ["/settings/preferences", "Tercihler"],
    ["/settings/notifications", "Bildirim ayarları"], ["/settings/integrations", "Entegrasyonlar"],
    ["/settings/import", "Veri içe aktar"], ["/settings/export", "Veri dışa aktar"],
    ["/settings/advanced", "Gelişmiş ayarlar"], ["/settings/about", "Hakkında"],
  ];

  function searchTypes() {
    const input = document.getElementById("global-search");
    try {
      if (input && input.form && window.Alpine) {
        const data = window.Alpine.$data(input.form);
        if (data && Array.isArray(data.mediaTypes)) {
          return { types: data.mediaTypes, selected: data.selectedType };
        }
      }
    } catch (e) {
      /* Alpine not ready */
    }
    const types = Array.from(document.querySelectorAll("aside a[data-efe-media-type]"))
      .filter((link) => link.dataset.efeMediaType !== "season")
      .map((link) => ({ value: link.dataset.efeMediaType, display: link.textContent.trim() }));
    return { types: types, selected: types[0] };
  }

  function commands(query) {
    const list = [];
    const add = (command) => list.push(command);
    const check = (on) => (on ? "✓" : "");

    // Search (only while typing)
    if (query) {
      const { types, selected } = searchTypes();
      const q = encodeURIComponent(query);
      if (selected) {
        add({ id: "search", group: "Ara", icon: svg("search"), title: "“" + query + "” ara",
              hint: selected.display, always: true,
              run: () => go("/search?q=" + q + "&media_type=" + encodeURIComponent(selected.value)) });
      }
      types.forEach((type) => {
        if (selected && type.value === selected.value) return;
        add({ id: "search-" + type.value, group: "Ara", icon: svg("search"),
              title: type.display + " içinde “" + query + "” ara", always: true, rank: 1,
              run: () => go("/search?q=" + q + "&media_type=" + encodeURIComponent(type.value)) });
      });
    }

    // Pages from the sidebar (with their own icons)
    const seen = {};
    document.querySelectorAll("aside a[href]").forEach((link) => {
      const path = new URL(link.href).pathname;
      const label = (link.querySelector("span:not([data-efe-unread-badge])") || link).textContent.trim();
      if (!label || seen[path]) return;
      seen[path] = true;
      const icon = link.querySelector("svg");
      add({ id: "page:" + path, group: "Sayfalar", title: label, icon: icon ? icon.outerHTML : svg("search"),
            run: () => go(link.getAttribute("href")) });
    });
    SETTINGS.forEach(([path, title]) => {
      if (seen[path]) return;
      add({ id: "page:" + path, group: "Ayarlar", title: title, icon: svg("gear"), keywords: "ayarlar settings",
            run: () => go(path) });
    });

    // Actions
    if (!anonymous) {
      add({ id: "create", group: "Eylemler", title: "Elle kayıt ekle", icon: svg("plus"), hint: "⌘N", run: () => go("/create") });
      add({ id: "random", group: "Eylemler", title: "Rastgele öneri", icon: svg("dice"), run: () => go("/rastgele") });
      add({ id: "read-all", group: "Eylemler", title: "Tüm bildirimleri okundu say", icon: svg("check"),
            run: () => markAllRead() });
      add({ id: "import", group: "Eylemler", title: "Veri içe aktar", icon: svg("upload"), keywords: "csv yamtrack trakt",
            run: () => go("/settings/import") });
      add({ id: "export", group: "Eylemler", title: "Veri dışa aktar", icon: svg("download"), keywords: "csv yedek",
            run: () => go("/settings/export") });
    }
    add({ id: "sidebar", group: "Eylemler", icon: svg("sidebar"),
          title: sidebarCollapsed() ? "Kenar çubuğunu genişlet" : "Kenar çubuğunu daralt", hint: "⌃⌘S",
          run: () => setSidebar(!sidebarCollapsed()) });
    add({ id: "sidebar-reset", group: "Eylemler", icon: svg("sort"), title: "Kenar çubuğu sırasını sıfırla",
          run: resetOrder });
    add({ id: "sound", group: "Eylemler", icon: svg("speaker"),
          title: efe.sound.enabled() ? "Ses efektlerini kapat" : "Ses efektlerini aç", hint: "⇧⌘M",
          run: () => efe.sound.setEnabled(!efe.sound.enabled()) });
    add({ id: "reload", group: "Eylemler", icon: svg("refresh"), title: "Sayfayı yenile", hint: "⌘R",
          run: () => window.location.reload() });

    // Appearance (shown while typing, or at the end)
    const preset = efe.theme.preset();
    THEMES.forEach(([id, title, color]) => add({
      id: "theme:" + id, group: "Tema", title: "Tema: " + title, icon: swatch(color), hint: check(preset === id),
      keywords: "tema theme görünüm renk", run: () => efe.theme.choose(id) }));
    const accent = efe.accent.current();
    ACCENTS.forEach(([id, title, color]) => add({
      id: "accent:" + id, group: "Vurgu rengi", title: "Vurgu rengi: " + title, icon: swatch(color),
      hint: check(accent === id), keywords: "vurgu accent renk", run: () => efe.accent.set(id) }));
    const font = efe.font.current();
    FONTS.forEach(([id, title]) => add({
      id: "font:" + id, group: "Yazı tipi", title: "Yazı tipi: " + title, icon: svg("type"),
      hint: check(font === id), keywords: "yazı tipi font", run: () => efe.font.set(id) }));
    const pack = efe.sound.pack();
    PACKS.forEach(([id, title]) => add({
      id: "pack:" + id, group: "Ses paketi", title: "Ses paketi: " + title, icon: svg("music"),
      hint: check(pack === id), keywords: "ses paketi sound", run: () => efe.sound.setPack(id) }));

    return list;
  }

  const RECENT_KEY = "efe-palette-recent";

  function recent() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function remember(id) {
    if (!id || id.indexOf("search") === 0) return;
    const ids = [id].concat(recent().filter((other) => other !== id)).slice(0, 5);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(ids));
    } catch (e) {
      /* storage unavailable */
    }
  }

  let palette = null;
  let results = [];
  let selected = 0;
  let previousFocus = null;

  function build() {
    palette = document.createElement("div");
    palette.className = "efe-palette";
    palette.hidden = true;
    palette.setAttribute("data-efe-silent", "");
    palette.innerHTML =
      '<div class="efe-palette-backdrop"></div>' +
      '<div class="efe-palette-box" role="dialog" aria-modal="true" aria-label="Komut paleti">' +
      '<label class="efe-palette-input">' + svg("search") +
      '<input type="text" autocomplete="off" spellcheck="false" placeholder="Sayfa, işlem ya da arama yaz…" ' +
      'role="combobox" aria-expanded="true" aria-controls="efe-palette-list">' +
      "<kbd>esc</kbd></label>" +
      '<div class="efe-palette-list" id="efe-palette-list" role="listbox"></div>' +
      '<div class="efe-palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> gez</span>' +
      "<span><kbd>↵</kbd> aç</span><span><kbd>esc</kbd> kapat</span></div></div>";
    document.body.appendChild(palette);
    const input = palette.querySelector("input");
    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", onKey);
    palette.querySelector(".efe-palette-backdrop").addEventListener("click", close);
    palette.querySelector(".efe-palette-list").addEventListener("mousemove", (event) => {
      const row = event.target.closest(".efe-palette-item");
      if (row && Number(row.dataset.index) !== selected) select(Number(row.dataset.index));
    });
    palette.querySelector(".efe-palette-list").addEventListener("click", (event) => {
      const row = event.target.closest(".efe-palette-item");
      if (row) run(Number(row.dataset.index));
    });
  }

  function render(raw) {
    const query = fold(raw.trim());
    const all = commands(raw.trim());
    if (!query) {
      const recentIds = recent();
      const recentItems = recentIds
        .map((id) => all.find((command) => command.id === id))
        .filter(Boolean)
        .map((command) => Object.assign({}, command, { group: "Son kullanılanlar" }));
      const rest = all.filter((command) => ["Sayfalar", "Eylemler"].indexOf(command.group) !== -1 &&
        recentIds.indexOf(command.id) === -1);
      results = recentItems.concat(rest);
    } else {
      results = all
        .map((command) => ({
          command: command,
          // Search rows stay available but rank below good page/action matches.
          score: command.always ? 45 - (command.rank || 0) :
            Math.max(score(query, command.title), command.keywords ? score(query, command.keywords) * 0.6 : 0),
        }))
        .filter((entry) => entry.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 40)
        .map((entry) => entry.command);
    }
    const listEl = palette.querySelector(".efe-palette-list");
    if (!results.length) {
      listEl.innerHTML = '<div class="efe-palette-empty">Sonuç yok</div>';
      return;
    }
    let html = "";
    let group = "";
    results.forEach((command, index) => {
      if (command.group !== group) {
        group = command.group;
        html += '<div class="efe-palette-group">' + escapeHtml(group) + "</div>";
      }
      html += '<div class="efe-palette-item" role="option" id="efe-palette-' + index + '" data-index="' + index + '">' +
        '<span class="efe-palette-icon">' + command.icon + "</span>" +
        '<span class="efe-palette-title">' + escapeHtml(command.title) + "</span>" +
        (command.hint ? '<span class="efe-palette-hint">' + escapeHtml(command.hint) + "</span>" : "") +
        "</div>";
    });
    listEl.innerHTML = html;
    select(0);
  }

  function select(index) {
    if (!results.length) return;
    selected = (index + results.length) % results.length;
    palette.querySelectorAll(".efe-palette-item").forEach((row) => {
      const on = Number(row.dataset.index) === selected;
      row.classList.toggle("is-selected", on);
      row.setAttribute("aria-selected", on ? "true" : "false");
      if (on) row.scrollIntoView({ block: "nearest" });
    });
    palette.querySelector("input").setAttribute("aria-activedescendant", "efe-palette-" + selected);
  }

  function run(index) {
    const command = results[index];
    if (!command) return;
    remember(command.id);
    close(true);
    efe.sound.play("nav");
    window.setTimeout(() => command.run(), 10);
  }

  function onKey(event) {
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      select(selected + 1);
      efe.sound.play("tick");
    } else if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      select(selected - 1);
      efe.sound.play("tick");
    } else if (event.key === "Enter") {
      event.preventDefault();
      run(selected);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  function open() {
    if (!palette) build();
    if (!palette.hidden) return;
    previousFocus = document.activeElement;
    palette.hidden = false;
    root.classList.add("efe-palette-open");
    const input = palette.querySelector("input");
    input.value = "";
    render("");
    efe.sound.play("menu");
    window.requestAnimationFrame(() => input.focus());
  }

  function close(silent) {
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    root.classList.remove("efe-palette-open");
    if (silent !== true) efe.sound.play("tap");
    if (previousFocus && previousFocus.focus) previousFocus.focus();
  }

  function toggle() {
    if (palette && !palette.hidden) close();
    else open();
  }

  // In the macOS app the menu bar owns ⌘K (Git → Komut Paleti), so only browsers bind it here.
  document.addEventListener("keydown", (event) => {
    if (root.classList.contains("efe-mac")) return;
    if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && (event.key === "k" || event.key === "K")) {
      event.preventDefault();
      toggle();
    }
  }, true);


  /* ------------------------------------------------------------------ */
  /* Context menu, drawn in the app's own colours (theme, accent, font) */
  /* Item: { label, icon, hint, checked, disabled, run, sub: [...] }    */
  /*       { header: "…" } · { sep: true }                              */
  /* ------------------------------------------------------------------ */
  const STATUSES = [
    ["All", "Tümü", "stack"], ["In progress", "Devam eden", "play"], ["Planning", "Planlanan", "bookmark"],
    ["Completed", "Tamamlanan", "check"], ["Paused", "Beklemede", "pause"], ["Dropped", "Bırakılan", "x"],
  ];
  const LIST_SORTS = [["score", "Puan"], ["title", "Ad"], ["progress", "İlerleme"], ["start_date", "Başlangıç tarihi"], ["end_date", "Bitiş tarihi"]];
  const HOME_SORTS = [["upcoming", "Yaklaşan"], ["recent", "Son etkinlik"], ["completion", "Tamamlanma"], ["episodes_left", "Kalan bölüm"], ["title", "Ad"]];
  Object.assign(ICONS, {
    open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    forward: '<path d="m9 18 6-6-6-6"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    filter: '<path d="M4 5h16l-6 8v5l-4 2v-7L4 5Z"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    eyeOff: '<path d="M3 3l18 18M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 8.5 4.5 9.5 6a17 17 0 0 1-3 3.4M6.6 7.6A16.7 16.7 0 0 0 2.5 12c1 1.5 4.5 6 9.5 6 1.6 0 3-.4 4.3-1.1M9.9 10a3 3 0 0 0 4.1 4.2"/>',
    eye: '<path d="M2.5 12c1-1.5 4.5-6 9.5-6s8.5 4.5 9.5 6c-1 1.5-4.5 6-9.5 6s-8.5-4.5-9.5-6Z"/><circle cx="12" cy="12" r="3"/>',
    command: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6Z"/>',
    top: '<path d="M5 4h14M12 20V9M7 13l5-5 5 5"/>',
    go: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    stack: '<path d="m12 3 9 5-9 5-9-5 9-5ZM3 13l9 5 9-5"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="m10 8.5 5 3.5-5 3.5v-7Z"/>',
    bookmark: '<path d="M6 4h12v17l-6-4-6 4V4Z"/>',
    pause: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
    x: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  });

  const isMac = root.classList.contains("efe-mac");
  const KEY = (mac, other) => (isMac ? mac : other);

  function copyText(text) {
    const done = () => efe.sound.play("tick");
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, () => legacyCopy(text, done));
    } else {
      legacyCopy(text, done);
    }
  }

  function legacyCopy(text, done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;opacity:0;top:0;left:0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (e) {
      efe.sound.play("error");
    }
    area.remove();
  }

  function focusSearch(type) {
    const input = document.getElementById("global-search");
    if (!input) return go("/search");
    try {
      if (type && window.Alpine && input.form) {
        const data = window.Alpine.$data(input.form);
        const match = (data.mediaTypes || []).find((entry) => entry.value === type);
        if (match) data.selectedType = match;
      }
    } catch (e) {
      /* Alpine not ready */
    }
    input.focus();
    input.select();
  }

  function pageState() {
    const element = document.getElementById("efe-page-state");
    return element ? element.dataset : {};
  }

  function themeItems() {
    const preset = efe.theme.preset();
    const make = ([id, title, color]) => ({ label: title, icon: swatch(color), checked: preset === id, run: () => efe.theme.choose(id) });
    return [
      ...THEMES.slice(0, 3).map(make),
      { sep: true }, { header: "Karanlık tonlar" }, ...THEMES.slice(3, 6).map(make),
      { sep: true }, { header: "Aydınlık tonlar" }, ...THEMES.slice(6).map(make),
    ];
  }

  function accentItems() {
    const current = efe.accent.current();
    const make = ([id, title, color]) => ({ label: title, icon: swatch(color), checked: current === id, run: () => efe.accent.set(id) });
    return [make(ACCENTS[0]), { sep: true }, ...ACCENTS.slice(1).map(make)];
  }

  function fontItems() {
    const current = efe.font.current();
    return FONTS.map(([id, title]) => ({ label: title, labelClass: "efe-font-" + id, checked: current === id, run: () => efe.font.set(id) }));
  }

  function soundItems() {
    const pack = efe.sound.pack();
    return [
      { label: "Ses efektleri", icon: "speaker", checked: efe.sound.enabled(), hint: KEY("⇧⌘M", ""),
        run: () => efe.sound.setEnabled(!efe.sound.enabled()) },
      { sep: true }, { header: "Ses paketi" },
      ...PACKS.map(([id, title]) => ({ label: title, checked: pack === id, run: () => efe.sound.setPack(id) })),
    ];
  }

  function appearanceItems() {
    return [
      { label: "Tema", icon: "palette", sub: themeItems() },
      { label: "Vurgu rengi", icon: "drop", sub: accentItems() },
      { label: "Yazı tipi", icon: "type", sub: fontItems() },
      { label: "Ses", icon: "speaker", sub: soundItems() },
    ];
  }

  function sidebarToolItems(item) {
    const items = [];
    if (item) {
      items.push({ label: "Kenar çubuğundan gizle", icon: "eyeOff", run: () => setHidden(keyOf(item), true) });
    }
    const hidden = hiddenItems();
    if (hidden.length) {
      items.push({ label: "Gizlenenler", icon: "eye", sub: [
        ...hidden.map((entry) => ({ label: entry.label, run: () => setHidden(entry.key, false) })),
        { sep: true },
        { label: "Tümünü göster", icon: "eye", run: showAllHidden },
      ] });
    }
    items.push(
      { label: sidebarCollapsed() ? "Kenar çubuğunu genişlet" : "Kenar çubuğunu daralt", icon: "sidebar",
        hint: KEY("⌃⌘S", ""), run: () => setSidebar(!sidebarCollapsed()) },
      { label: "Sırayı sıfırla", icon: "sort", disabled: !efe.prefs.get("order"), run: resetOrder },
    );
    return items;
  }

  function mediaListItems(base, here, state) {
    return [
      { label: "Durum", icon: "filter", sub: STATUSES.map(([value, title, icon]) => ({
        label: title, icon: icon, checked: here && state.status === value,
        run: () => go(base + "?status=" + encodeURIComponent(value)) })) },
      { label: "Sırala", icon: "sort", sub: LIST_SORTS.map(([value, title]) => ({
        label: title, checked: here && state.sort === value, run: () => go(base + "?sort=" + value) })) },
      { label: "Görünüm", icon: "grid", sub: [
        { label: "Izgara", icon: "grid", checked: here && state.layout === "grid", run: () => go(base + "?layout=grid") },
        { label: "Tablo", icon: "list", checked: here && state.layout === "table", run: () => go(base + "?layout=table") },
      ] },
    ];
  }

  function goItems() {
    return Array.from(document.querySelectorAll("[data-efe-sortable] > li"))
      .filter((item) => !item.hidden)
      .map((item, index) => {
        const link = item.querySelector("a[href]");
        const icon = link && link.querySelector("svg");
        return link ? { label: itemLabel(item), icon: icon ? icon.outerHTML : "go",
                        hint: isMac && index < 9 ? "⌘" + (index + 1) : "", run: () => go(link.getAttribute("href")) } : null;
      })
      .filter(Boolean);
  }

  function sidebarMenu(target) {
    const item = target.closest("[data-efe-sortable] > li");
    const link = target.closest("a[href]");
    if (!link || !item) {
      return [
        ...sidebarToolItems(null),
        { label: "Kenar çubuğunu düzenle…", icon: "gear", run: () => go("/settings/preferences") },
        { sep: true },
        ...appearanceItems().slice(0, 3),
      ];
    }
    const href = link.getAttribute("href");
    const base = new URL(link.href).pathname;
    const state = pageState();
    const type = link.dataset.efeMediaType || "";
    const items = [{ header: itemLabel(item) }, { label: "Aç", icon: "open", run: () => go(href) }];
    if (type) {
      items.push(...mediaListItems(base, state.mediaType === type, state), { sep: true },
        { label: itemLabel(item) + " içinde ara…", icon: "search", run: () => focusSearch(type) },
        { label: "Elle kayıt ekle…", icon: "plus", run: () => go("/create") });
    } else if (base === "/bildirimler") {
      items.push({ label: "Tümünü okundu say", icon: "check", run: markAllRead });
    } else if (base === "/rastgele") {
      items.push({ label: "Bana bir şey öner", icon: "dice", run: () => go("/rastgele") });
    } else if (base === "/calendar") {
      items.push({ label: "Izgara görünümü", icon: "calendar", run: () => go("/calendar?view=grid") },
        { label: "Liste görünümü", icon: "list", run: () => go("/calendar?view=list") });
    }
    items.push({ sep: true }, ...sidebarToolItems(item), { sep: true },
      { label: "Adresi kopyala", icon: "link", run: () => copyText(link.href) });
    return items;
  }

  function markAllRead() {
    efe.post("/bildirimler/okundu", {}).then((response) => {
      if (!response || !response.ok) {
        efe.sound.play("error");
        return;
      }
      document.dispatchEvent(new Event("efe-notifications-read"));
      efe.sound.play("success");
      if (window.location.pathname.indexOf("/bildirimler") === 0) window.location.reload();
    });
  }

  function pageMenu(target) {
    const items = [
      { label: "Geri", icon: "back", hint: KEY("⌘[", ""), run: () => window.history.back() },
      { label: "İleri", icon: "forward", hint: KEY("⌘]", ""), run: () => window.history.forward() },
      { label: "Yeniden yükle", icon: "refresh", hint: KEY("⌘R", ""), run: () => window.location.reload() },
      { sep: true },
    ];
    const link = target.closest("a[href]");
    if (link && !/^(javascript|mailto):/i.test(link.getAttribute("href") || "")) {
      const url = new URL(link.href);
      const external = url.origin !== window.location.origin;
      items.push(
        external ? { label: "Tarayıcıda aç", icon: "open", run: () => window.open(url.href, "_blank") }
                 : { label: "Bağlantıyı aç", icon: "open", run: () => go(url.pathname + url.search) },
        { label: "Bağlantıyı kopyala", icon: "link", run: () => copyText(url.href) },
        { sep: true },
      );
    }
    const state = pageState();
    if (!anonymous && state.page === "home") {
      const hidden = state.hideUnreleased === "1";
      items.push(
        { label: "Sırala", icon: "sort", sub: HOME_SORTS.map(([value, title]) => ({
          label: title, checked: state.sort === value, run: () => go("/?sort=" + value) })) },
        { label: "Yayınlanmamışları gizle", icon: "eyeOff", checked: hidden,
          run: () => go("/?hide_unreleased=" + (hidden ? "false" : "true")) },
        { sep: true },
      );
    } else if (!anonymous && state.page === "medialist") {
      items.push(...mediaListItems(window.location.pathname, true, state), { sep: true });
    }
    if (!anonymous) {
      items.push(
        { label: "Komut paleti…", icon: "command", hint: KEY("⌘K", "Ctrl K"), run: open },
        { label: "Ara…", icon: "search", hint: KEY("⌘F", "/"), run: () => focusSearch() },
        { label: "Elle kayıt ekle…", icon: "plus", hint: KEY("⌘N", ""), run: () => go("/create") },
        { label: "Rastgele öneri", icon: "dice", run: () => go("/rastgele") },
        { label: "Git", icon: "go", sub: goItems() },
        { sep: true },
        ...appearanceItems(),
        { sep: true },
      );
    }
    if ((window.scrollY || document.documentElement.scrollTop) > 40) {
      items.push({ label: "Sayfanın başına dön", icon: "top", run: () => window.scrollTo({ top: 0, behavior: "smooth" }) });
    }
    items.push({ label: "Adresi kopyala", icon: "link", run: () => copyText(window.location.href) });
    return items;
  }

  let panels = [];
  let menuOpenTimer = 0;

  function iconHtml(icon) {
    if (!icon) return "";
    return icon.charAt(0) === "<" ? icon : svg(icon);
  }

  function renderPanel(items, level) {
    const panel = document.createElement("div");
    panel.className = "efe-cm";
    panel.setAttribute("role", "menu");
    panel.dataset.level = String(level);
    const hasChecks = items.some((item) => item.checked !== undefined);
    items = items.filter((item, index, all) =>
      !(item.sep && (index === 0 || index === all.length - 1 || (all[index - 1] && all[index - 1].sep))));
    items.forEach((item) => {
      if (item.sep) {
        panel.insertAdjacentHTML("beforeend", '<div class="efe-cm-sep" role="separator"></div>');
        return;
      }
      if (item.header) {
        panel.insertAdjacentHTML("beforeend", '<div class="efe-cm-header">' + escapeHtml(item.header) + "</div>");
        return;
      }
      const row = document.createElement("div");
      row.className = "efe-cm-item" + (item.disabled ? " is-disabled" : "");
      row.setAttribute("role", item.checked !== undefined ? "menuitemcheckbox" : "menuitem");
      if (item.checked !== undefined) row.setAttribute("aria-checked", item.checked ? "true" : "false");
      if (item.disabled) row.setAttribute("aria-disabled", "true");
      row.innerHTML =
        (hasChecks ? '<span class="efe-cm-check">' + (item.checked ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>' : "") + "</span>" : "") +
        '<span class="efe-cm-icon">' + iconHtml(item.icon) + "</span>" +
        '<span class="efe-cm-label' + (item.labelClass ? " " + item.labelClass : "") + '">' + escapeHtml(item.label) + "</span>" +
        (item.sub ? '<span class="efe-cm-chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg></span>'
                  : item.hint ? '<span class="efe-cm-hint">' + escapeHtml(item.hint) + "</span>" : "");
      row._efeItem = item;
      panel.appendChild(row);
    });
    return panel;
  }

  function placePanel(panel, x, y, anchor) {
    document.body.appendChild(panel);
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = x;
    let top = y;
    if (anchor) {
      const rect = anchor.getBoundingClientRect();
      left = rect.right - 3;
      top = rect.top - 5;
      if (left + width > vw - 6) left = Math.max(6, rect.left - width + 3);
    } else if (left + width > vw - 6) {
      left = Math.max(6, x - width);
    }
    if (top + height > vh - 6) top = Math.max(6, (anchor ? vh - 6 : y) - height);
    panel.style.left = left + "px";
    panel.style.top = top + "px";
  }

  function closeMenus(fromLevel) {
    const level = fromLevel || 0;
    panels.filter((panel) => Number(panel.dataset.level) >= level).forEach((panel) => panel.remove());
    panels = panels.filter((panel) => Number(panel.dataset.level) < level);
    if (!panels.length) root.classList.remove("efe-cm-open");
  }

  function activeRow(panel) {
    return panel && panel.querySelector(".efe-cm-item.is-active");
  }

  function setActive(panel, row) {
    panel.querySelectorAll(".efe-cm-item.is-active").forEach((other) => other.classList.remove("is-active"));
    if (row) row.classList.add("is-active");
  }

  function openSub(row) {
    const panel = row.closest(".efe-cm");
    const level = Number(panel.dataset.level) + 1;
    closeMenus(level);
    const item = row._efeItem;
    if (!item || !item.sub || item.disabled || !item.sub.length) return null;
    const sub = renderPanel(item.sub, level);
    placePanel(sub, 0, 0, row);
    panels.push(sub);
    row.classList.add("is-open");
    return sub;
  }

  function runRow(row) {
    const item = row && row._efeItem;
    if (!item || item.disabled) return;
    if (item.sub) {
      const sub = openSub(row);
      if (sub) setActive(sub, sub.querySelector(".efe-cm-item:not(.is-disabled)"));
      return;
    }
    closeMenus(0);
    efe.sound.play("click");
    window.setTimeout(() => item.run && item.run(), 0);
  }

  function showMenu(items, x, y) {
    closeMenus(0);
    if (palette && !palette.hidden) close(true);
    const panel = renderPanel(items, 0);
    placePanel(panel, x, y, null);
    panels.push(panel);
    root.classList.add("efe-cm-open");
    efe.sound.play("menu");
  }

  document.addEventListener("contextmenu", (event) => {
    if (event.defaultPrevented || event.shiftKey) return;
    const target = event.target instanceof Element ? event.target : event.target && event.target.parentElement;
    if (!target || target.closest(".efe-cm, .efe-palette")) {
      event.preventDefault();
      return;
    }
    if (target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false']), video, audio")) return;
    const selection = window.getSelection && window.getSelection();
    if (selection && !selection.isCollapsed && String(selection).trim()) return;
    event.preventDefault();
    const items = target.closest("aside") && !anonymous ? sidebarMenu(target) : pageMenu(target);
    showMenu(items, event.clientX, event.clientY);
  });

  document.addEventListener("mousemove", (event) => {
    const row = event.target.closest && event.target.closest(".efe-cm-item");
    if (!row) return;
    const panel = row.closest(".efe-cm");
    if (row.classList.contains("is-active")) return;
    setActive(panel, row);
    window.clearTimeout(menuOpenTimer);
    const level = Number(panel.dataset.level);
    menuOpenTimer = window.setTimeout(() => {
      if (!row.isConnected) return;
      panel.querySelectorAll(".efe-cm-item.is-open").forEach((other) => other !== row && other.classList.remove("is-open"));
      if (row._efeItem && row._efeItem.sub) openSub(row);
      else closeMenus(level + 1);
    }, row._efeItem && row._efeItem.sub ? 90 : 160);
  });

  document.addEventListener("mousedown", (event) => {
    if (!panels.length) return;
    if (event.target.closest && event.target.closest(".efe-cm")) {
      event.preventDefault();
      return;
    }
    // Like a native menu: a click outside only closes the menu, it does not reach the page.
    closeMenus(0);
    event.preventDefault();
    event.stopPropagation();
    const swallow = (click) => {
      click.preventDefault();
      click.stopPropagation();
    };
    document.addEventListener("click", swallow, { capture: true, once: true });
    window.setTimeout(() => document.removeEventListener("click", swallow, true), 400);
  }, true);

  document.addEventListener("click", (event) => {
    const row = event.target.closest && event.target.closest(".efe-cm-item");
    if (!row) return;
    event.preventDefault();
    event.stopPropagation();
    runRow(row);
  }, true);

  document.addEventListener("keydown", (event) => {
    if (!panels.length) return;
    const panel = panels[panels.length - 1];
    const rows = Array.from(panel.querySelectorAll(".efe-cm-item:not(.is-disabled)"));
    const current = activeRow(panel);
    const index = rows.indexOf(current);
    const handled = ["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Enter", " ", "Escape"].indexOf(event.key) !== -1;
    if (!handled) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "ArrowDown") setActive(panel, rows[(index + 1) % rows.length]);
    else if (event.key === "ArrowUp") setActive(panel, rows[(index - 1 + rows.length) % rows.length]);
    else if (event.key === "ArrowRight" && current && current._efeItem.sub) runRow(current);
    else if (event.key === "ArrowLeft" || event.key === "Escape") {
      if (panels.length > 1 && event.key === "ArrowLeft") closeMenus(panels.length - 1);
      else if (event.key === "Escape") closeMenus(0);
    } else if ((event.key === "Enter" || event.key === " ") && current) runRow(current);
    efe.sound.play("tick");
  }, true);

  ["scroll", "resize", "blur"].forEach((name) => window.addEventListener(name, () => {
    if (panels.length && name !== "scroll") closeMenus(0);
  }));
  document.addEventListener("scroll", (event) => {
    if (panels.length && !(event.target.closest && event.target.closest(".efe-cm"))) closeMenus(0);
  }, true);

  efe.palette = { open: open, close: close, toggle: toggle };
  efe.menu = { close: () => closeMenus(0) };
  efe.sidebar = {
    collapsed: sidebarCollapsed,
    set: setSidebar,
    toggle: () => setSidebar(!sidebarCollapsed()),
    resetOrder: resetOrder,
    hide: (key) => setHidden(key, true),
    show: (key) => setHidden(key, false),
    showAll: showAllHidden,
    hidden: hiddenItems,
  };

  // Seasonal effects: a few small SVG pieces each with its own random path, so they look natural.
  // Built only when the effect is turned on; only transform animates (compositor, no repaint).
  const SEASONAL = ["leaves", "blossoms", "bats"];
  const LEAF = [
    '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2c4.5 3.6 7 7.6 6.4 12.1C17.9 18.4 15 21 12 22c-3-1-5.9-3.6-6.4-7.9C5 9.6 7.5 5.6 12 2z"/><path d="M12 5.5V21M12 10l3-2.4M12 14l-3.2-2.6M12 17.5l2.8-2.2" stroke="rgba(0,0,0,.22)" stroke-width="1" fill="none" stroke-linecap="round"/></svg>',
    '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2l1.6 4.2 3.2-1.4-.9 3.8 3.9.4-2.6 2.9 2.2 1.8-4 .8.5 3.2-3.4-1.6-.5 4.9h-1l-.5-4.9-3.4 1.6.5-3.2-4-.8 2.2-1.8-2.6-2.9 3.9-.4-.9-3.8 3.2 1.4z"/></svg>',
  ];
  const BLOSSOM = '<svg viewBox="0 0 24 24"><g fill="currentColor"><ellipse cx="12" cy="6.6" rx="3.3" ry="4.6"/><ellipse cx="12" cy="6.6" rx="3.3" ry="4.6" transform="rotate(72 12 12)"/><ellipse cx="12" cy="6.6" rx="3.3" ry="4.6" transform="rotate(144 12 12)"/><ellipse cx="12" cy="6.6" rx="3.3" ry="4.6" transform="rotate(216 12 12)"/><ellipse cx="12" cy="6.6" rx="3.3" ry="4.6" transform="rotate(288 12 12)"/></g><circle cx="12" cy="12" r="2.1" fill="#fcd34d"/></svg>';
  const BAT_HALF = "M32 8.5L33.6 5.6 34.6 9.4C40 6.6 48 5.2 54 6.4 58 7.2 61.5 9.4 63.5 12.4 60 11.8 57.4 12.8 56.4 15.2 54 13.6 50.6 13.6 48.6 15.8 46.4 14.4 43.2 14.8 41.4 17.2 39.4 18 37.4 20 35.6 23.4 34.6 25.4 33.4 26.6 32 27Z";
  const BAT = '<svg viewBox="0 0 64 32"><g fill="currentColor"><path d="' + BAT_HALF + '"/><path d="' + BAT_HALF + '" transform="matrix(-1 0 0 1 64 0)"/></g></svg>';
  const PALETTE = {
    leaves: { dark: ["#f59e0b", "#ea580c", "#c2410c", "#d97706", "#b91c1c", "#ca8a04"], light: ["#d97706", "#ea580c", "#c2410c", "#b45309", "#b91c1c"] },
    blossoms: { dark: ["#fbcfe8", "#f9a8d4", "#fce7f3", "#f472b6"], light: ["#f472b6", "#ec4899", "#f9a8d4", "#db2777"] },
    bats: { dark: ["#c4b5fd", "#a78bfa", "#ddd6fe"], light: ["#3b3054", "#4c3d6b", "#2e2640"] },
  };
  function rand(min, max) { return min + Math.random() * (max - min); }
  function pick(list) { return list[Math.floor(Math.random() * list.length)]; }
  function buildSeason(name) {
    const box = document.getElementById("efe-fx-" + name);
    if (!box || box.childElementCount) return;
    const small = window.innerWidth < 640;
    const count = name === "bats" ? (small ? 4 : 7) : (small ? 9 : 16);
    const html = [];
    for (let i = 0; i < count; i += 1) {
      const dark = pick(PALETTE[name].dark), light = pick(PALETTE[name].light);
      if (name === "bats") {
        const size = rand(26, 46);
        const style = "--x:" + rand(5, 85).toFixed(1) + "vw;--y:" + rand(4, 58).toFixed(1) + "vh;--s:" + size.toFixed(0) + "px;--d:" + rand(16, 28).toFixed(1) +
          "s;--delay:" + (-rand(0, 28)).toFixed(1) + "s;--rise:" + rand(-18, 6).toFixed(1) + "vh;--o:" + rand(.45, .75).toFixed(2) +
          ";--flap:" + rand(.16, .26).toFixed(2) + "s;--c:" + dark + ";--cl:" + light;
        html.push('<span class="efe-fx-bat' + (i % 2 ? " rtl" : "") + '" style="' + style + '"><span>' + BAT + "</span></span>");
      } else {
        const leaf = name === "leaves";
        const size = leaf ? rand(15, 28) : rand(11, 19);
        const style = "--x:" + rand(-2, 100).toFixed(1) + "vw;--y:" + rand(5, 90).toFixed(1) + "vh;--s:" + size.toFixed(0) +
          "px;--d:" + (leaf ? rand(11, 21) : rand(14, 26)).toFixed(1) + "s;--delay:" + (-rand(0, 26)).toFixed(1) +
          "s;--drift:" + rand(-14, 18).toFixed(1) + "vw;--amp:" + rand(10, 34).toFixed(0) + "px;--sw:" + rand(1.8, 3.6).toFixed(1) +
          "s;--r0:" + rand(-70, -10).toFixed(0) + "deg;--r1:" + rand(20, 90).toFixed(0) + "deg;--o:" + rand(.6, .92).toFixed(2) +
          ";--c:" + dark + ";--cl:" + light;
        html.push('<span class="efe-fx-piece" style="' + style + '"><span>' + (leaf ? pick(LEAF) : BLOSSOM) + "</span></span>");
      }
    }
    box.innerHTML = html.join("");
  }
  function buildActiveSeasons() {
    SEASONAL.forEach((name) => { if (document.documentElement.hasAttribute("data-fx-" + name)) buildSeason(name); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", buildActiveSeasons); else buildActiveSeasons();

  // Ambient effects: snow (its own setting, with the 1 Dec – 6 Jan default) and the others in "efe-fx".
  const FX = ["rain", "stars", "fireflies", "aurora", "grain"];
  efe.fx = {
    names: FX.concat(["snow"], SEASONAL),
    inSeason(name) { return (document.documentElement.getAttribute("data-season") || "").split(" ").indexOf(name) !== -1; },
    on(name) {
      const root = document.documentElement;
      return name === "snow" ? root.hasAttribute("data-snow") : root.hasAttribute("data-fx-" + name);
    },
    set(name, on) {
      const root = document.documentElement;
      if (name === "snow") {
        try { localStorage.setItem("efe-xmas", on ? "on" : "off"); } catch (e) { /* private mode */ }
        if (on) { root.dataset.snow = "1"; delete root.dataset.xmasOff; } else { delete root.dataset.snow; root.dataset.xmasOff = "1"; }
      } else if (SEASONAL.indexOf(name) !== -1) {
        if (on) root.setAttribute("data-fx-" + name, ""); else root.removeAttribute("data-fx-" + name);
        if (on) buildSeason(name);
        try {
          const prefs = JSON.parse(localStorage.getItem("efe-mevsim") || "{}") || {};
          // Off in its season: only until next year's season. On: all year.
          if (on) prefs[name] = "on"; else prefs[name] = this.inSeason(name) ? "off:" + new Date().getFullYear() : "";
          if (!prefs[name] || (on && this.inSeason(name))) delete prefs[name];
          localStorage.setItem("efe-mevsim", JSON.stringify(prefs));
        } catch (e) { /* private mode */ }
      } else if (FX.indexOf(name) !== -1) {
        if (on) root.setAttribute("data-fx-" + name, ""); else root.removeAttribute("data-fx-" + name);
        const active = FX.filter((key) => root.hasAttribute("data-fx-" + key));
        try { localStorage.setItem("efe-fx", active.join(",")); } catch (e) { /* private mode */ }
      } else {
        return;
      }
      window.dispatchEvent(new CustomEvent("efe-fx-changed", { detail: { name: name, on: !!on } }));
    },
  };

  // Grid density of the media lists: compact / comfortable (default) / gallery, kept in this browser.
  efe.gridDensity = {
    get() { return document.documentElement.dataset.grid || "comfortable"; },
    set(value) {
      const root = document.documentElement;
      if (value === "compact" || value === "gallery") root.dataset.grid = value; else delete root.dataset.grid;
      try {
        if (value === "compact" || value === "gallery") localStorage.setItem("efe-grid", value); else localStorage.removeItem("efe-grid");
      } catch (e) { /* private mode */ }
      if (efe.sound) efe.sound.play("tap");
    },
  };

  // Cinema mode: one big "watch this now" picture on the home page (kept in this browser).
  efe.cinema = {
    on: () => document.documentElement.dataset.cinema === "1",
    set(on) {
      const root = document.documentElement;
      try { localStorage.setItem("efe-cinema", on ? "on" : "off"); } catch (e) { /* private mode */ }
      if (on) root.dataset.cinema = "1"; else delete root.dataset.cinema;
      delete root.dataset.cinemaPeek;
      window.dispatchEvent(new CustomEvent("efe-cinema-changed", { detail: { on: !!on } }));
    },
    peek(show) {
      const root = document.documentElement;
      if (show) root.dataset.cinemaPeek = "1"; else delete root.dataset.cinemaPeek;
    },
  };
})();
