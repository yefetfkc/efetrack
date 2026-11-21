/*
 * efeTrack client helpers: theme switching, sound effects and the
 * notification center badge / browser notifications.
 */
(function () {
  "use strict";

  const root = document.documentElement;

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    const input = document.querySelector("input[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function post(url, data) {
    const body = new URLSearchParams(data);
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      credentials: "same-origin",
    }).catch(() => null);
  }

  function storageGet(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (e) {
      return fallback;
    }
  }

  function storageSet(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      /* storage can be unavailable (private mode) */
    }
  }

  /* ------------------------------------------------------------------ */
  /* Theme: mode (dark / light / system, saved on the server) plus a    */
  /* colour tone and a font (saved in this browser / the Mac app).      */
  /* ------------------------------------------------------------------ */
  const THEMES = ["dark", "light", "system"];
  const TONES = ["red", "purple", "blue", "oled"];
  const FONTS = ["system", "inter", "jakarta", "manrope", "nunito", "figtree"];
  const PRESETS = {
    system: { mode: "system", tone: "" },
    light: { mode: "light", tone: "" },
    dark: { mode: "dark", tone: "" },
    "dark-red": { mode: "dark", tone: "red" },
    "dark-purple": { mode: "dark", tone: "purple" },
    "dark-blue": { mode: "dark", tone: "blue" },
    "dark-oled": { mode: "dark", tone: "oled" },
    "light-red": { mode: "light", tone: "red" },
    "light-purple": { mode: "light", tone: "purple" },
    "light-blue": { mode: "light", tone: "blue" },
  };
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

  function currentTheme() {
    const theme = root.dataset.theme;
    if (!theme || theme === "anon") return storageGet("efe-theme", "dark");
    return theme;
  }

  function currentTone() {
    return TONES.indexOf(root.dataset.tone) === -1 ? "" : root.dataset.tone;
  }

  function currentFont() {
    return FONTS.indexOf(root.dataset.font) === -1 ? "system" : root.dataset.font;
  }

  function currentPreset() {
    const mode = currentTheme();
    const tone = currentTone();
    return tone ? mode + "-" + tone : mode;
  }

  /* Small preferences kept in this browser (and mirrored by the macOS app). */
  const PREFS = {
    tone: "efe-tone",
    font: "efe-font",
    accent: "efe-accent",
    pack: "efe-sound-pack",
    clickVolume: "efe-click-volume",
    sidebar: "efe-sidebar",
    order: "efe-sidebar-order",
    hidden: "efe-sidebar-hidden",
  };

  function prefsAll() {
    const all = {};
    Object.keys(PREFS).forEach((key) => {
      all[key] = storageGet(PREFS[key], "") || "";
    });
    return all;
  }

  const prefs = {
    get(key) {
      return PREFS[key] ? storageGet(PREFS[key], "") || "" : "";
    },
    set(key, value) {
      if (!PREFS[key]) return;
      storageSet(PREFS[key], value === null || value === undefined ? "" : String(value));
      window.dispatchEvent(new CustomEvent("efe-prefs-changed", { detail: prefsAll() }));
    },
    all: prefsAll,
  };

  const ACCENTS = ["indigo", "blue", "purple", "pink", "red", "orange", "amber", "green", "teal", "graphite"];

  function currentAccent() {
    return ACCENTS.indexOf(root.dataset.accent) === -1 ? "auto" : root.dataset.accent;
  }

  function appearanceDetail(light) {
    const styles = window.getComputedStyle(root);
    return {
      light: light,
      theme: currentTheme(),
      tone: currentTone(),
      font: currentFont(),
      accent: currentAccent(),
      prefs: prefsAll(),
      preset: currentPreset(),
      chrome: (styles.getPropertyValue("--ef-chrome") || "").trim(),
      page: (styles.getPropertyValue("--ef-page") || "").trim(),
    };
  }

  function applyTheme(theme, animate) {
    const light = theme === "light" || (theme === "system" && media && media.matches);
    // Animate only when the page is on screen and focused: WebKit pauses transitions in
    // background windows, which would leave parts of the page in the old colours.
    const canAnimate = animate && document.visibilityState === "visible" &&
      (!document.hasFocus || document.hasFocus()) &&
      !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    root.classList.toggle("theme-transition", canAnimate);
    window.clearTimeout(applyTheme.timer);
    if (canAnimate) applyTheme.timer = window.setTimeout(() => root.classList.remove("theme-transition"), 350);
    root.classList.toggle("light", light);
    root.classList.toggle("scheme-light", light);
    root.classList.toggle("scheme-dark", !light);
    restyleAll();
    const detail = appearanceDetail(light);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", detail.chrome || (light ? "#ffffff" : "#1a1d20"));
    window.dispatchEvent(new CustomEvent("efe-theme-applied", { detail: detail }));
  }

  /* Make WebKit recompute every element after a colour change. Without this it can keep
     some elements (sticky bars, cards) in the old colours until they are repainted. */
  function restyleAll() {
    root.classList.add("efe-restyle");
    void root.offsetHeight;
    root.classList.remove("efe-restyle");
    void root.offsetHeight;
  }

  function announce() {
    window.dispatchEvent(new CustomEvent("efe-theme-changed", {
      detail: {
        theme: currentTheme(),
        tone: currentTone(),
        font: currentFont(),
        accent: currentAccent(),
        preset: currentPreset(),
      },
    }));
  }

  function setTheme(theme, save, quiet) {
    if (THEMES.indexOf(theme) === -1) return;
    const anonymous = root.dataset.theme === "anon";
    if (!anonymous) root.dataset.theme = theme;
    storageSet("efe-theme", theme);
    applyTheme(theme, true);
    announce();
    if (!quiet) sound.play("switch");
    if (save !== false && !anonymous) post("/tercihler/tema", { theme: theme });
  }

  function setTone(tone) {
    if (TONES.indexOf(tone) === -1) {
      delete root.dataset.tone;
      prefs.set("tone", "");
    } else {
      root.dataset.tone = tone;
      prefs.set("tone", tone);
    }
  }

  function chooseTheme(id) {
    const preset = PRESETS[id];
    if (!preset) return;
    setTone(preset.tone);
    setTheme(preset.mode);
  }

  function setFont(font) {
    if (FONTS.indexOf(font) === -1) return;
    if (font === "system") delete root.dataset.font;
    else root.dataset.font = font;
    prefs.set("font", font === "system" ? "" : font);
    sound.play("switch");
    applyTheme(currentTheme(), false);
    announce();
  }

  function setAccent(accent) {
    if (ACCENTS.indexOf(accent) === -1) {
      delete root.dataset.accent;
      prefs.set("accent", "");
    } else {
      root.dataset.accent = accent;
      prefs.set("accent", accent);
    }
    sound.play("switch");
    applyTheme(currentTheme(), false);
    announce();
  }

  if (media) {
    const onChange = () => {
      if (currentTheme() === "system") applyTheme("system", true);
    };
    if (media.addEventListener) media.addEventListener("change", onChange);
    else if (media.addListener) media.addListener(onChange);
  }

  const theme = {
    current: currentTheme,
    tone: currentTone,
    preset: currentPreset,
    presets: Object.keys(PRESETS),
    set: setTheme,
    choose: chooseTheme,
    setTone(tone) {
      setTone(tone);
      setTheme(currentTheme());
    },
    cycle() {
      const index = THEMES.indexOf(currentTheme());
      setTheme(THEMES[(index + 1) % THEMES.length]);
    },
    apply() {
      applyTheme(currentTheme(), false);
    },
  };

  const font = { current: currentFont, set: setFont, names: FONTS };
  const accent = { current: currentAccent, set: setAccent, names: ["auto"].concat(ACCENTS) };

  /* ------------------------------------------------------------------ */
  /* Sound effects (synthesised with the Web Audio API, no files).      */
  /* Soft, short and rounded: sine/triangle tones through a low-pass    */
  /* filter and a compressor, plus tiny filtered-noise "clicks".        */
  /* ------------------------------------------------------------------ */
  let audioContext = null;
  let master = null;
  let noiseBuffer = null;

  function audio() {
    if (!audioContext) {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) return null;
      audioContext = new Context();
      const compressor = audioContext.createDynamicsCompressor();
      compressor.threshold.value = -18;
      compressor.knee.value = 12;
      compressor.ratio.value = 4;
      const warmth = audioContext.createBiquadFilter();
      warmth.type = "lowpass";
      warmth.frequency.value = 7000;
      warmth.connect(compressor);
      compressor.connect(audioContext.destination);
      master = warmth;
    }
    if (audioContext.state === "suspended") audioContext.resume();
    return audioContext;
  }

  function soundEnabled() {
    return root.dataset.sound !== "0";
  }

  /* Interface sounds (clicks, menus) have their own volume; event sounds use the main one. */
  const UI_SOUNDS = ["click", "nav", "tap", "menu", "tick", "switch", "slide"];
  let playingUi = false;

  function clamp(value, fallback) {
    const number = parseInt(value, 10);
    return Math.max(0, Math.min(100, isNaN(number) ? fallback : number)) / 100;
  }

  function volume() {
    if (playingUi) return clamp(prefs.get("clickVolume"), 60);
    return clamp(root.dataset.volume || "60", 60);
  }

  function tone(ctx, options) {
    const start = ctx.currentTime + (options.delay || 0);
    const duration = options.duration || 0.15;
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = options.type || "sine";
    oscillator.frequency.setValueAtTime(options.from, start);
    if (options.to) oscillator.frequency.exponentialRampToValueAtTime(options.to, start + duration * 0.8);
    const peak = (options.gain || 0.2) * volume();
    const attack = options.attack || Math.min(0.008, duration / 4);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(Math.max(peak, 0.0002), start + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(master);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.05);
  }

  function noise(ctx, options) {
    if (!noiseBuffer) {
      noiseBuffer = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.25), ctx.sampleRate);
      const data = noiseBuffer.getChannelData(0);
      for (let index = 0; index < data.length; index += 1) data[index] = Math.random() * 2 - 1;
    }
    const start = ctx.currentTime + (options.delay || 0);
    const duration = options.duration || 0.012;
    const source = ctx.createBufferSource();
    source.buffer = noiseBuffer;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = options.freq || 3000;
    filter.Q.value = options.q || 1.4;
    const gain = ctx.createGain();
    const peak = (options.gain || 0.2) * volume();
    gain.gain.setValueAtTime(Math.max(peak, 0.0002), start);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    source.start(start);
    source.stop(start + duration + 0.02);
  }

  const SOFT = {
    /* soft tactile click for buttons and links */
    click(ctx) {
      noise(ctx, { freq: 3400, q: 1.6, duration: 0.012, gain: 0.22 });
      tone(ctx, { from: 1650, to: 1250, duration: 0.03, type: "sine", gain: 0.05 });
    },
    /* gentle wooden "pop" for sidebar / page navigation */
    nav(ctx) {
      noise(ctx, { freq: 1800, q: 2.2, duration: 0.01, gain: 0.12 });
      tone(ctx, { from: 620, to: 880, duration: 0.07, type: "sine", gain: 0.12 });
    },
    tap(ctx) {
      noise(ctx, { freq: 2600, q: 1.8, duration: 0.012, gain: 0.16 });
      tone(ctx, { from: 900, to: 760, duration: 0.05, type: "sine", gain: 0.08 });
    },
    /* context menu / dropdown opening */
    menu(ctx) {
      tone(ctx, { from: 540, to: 720, duration: 0.06, type: "sine", gain: 0.07 });
    },
    switch(ctx) {
      tone(ctx, { from: 880, duration: 0.05, type: "sine", gain: 0.09 });
      tone(ctx, { from: 1320, duration: 0.08, type: "sine", gain: 0.08, delay: 0.055 });
      noise(ctx, { freq: 4200, q: 2, duration: 0.008, gain: 0.08 });
    },
    success(ctx) {
      tone(ctx, { from: 783.99, duration: 0.12, type: "sine", gain: 0.15 });
      tone(ctx, { from: 1046.5, duration: 0.22, type: "sine", gain: 0.13, delay: 0.08 });
      tone(ctx, { from: 2093, duration: 0.18, type: "sine", gain: 0.02, delay: 0.08 });
    },
    complete(ctx) {
      [523.25, 659.25, 783.99, 1046.5].forEach((frequency, index) => {
        tone(ctx, { from: frequency, duration: 0.3, type: "triangle", gain: 0.11, delay: index * 0.075 });
        tone(ctx, { from: frequency * 2, duration: 0.2, type: "sine", gain: 0.025, delay: index * 0.075 });
      });
      tone(ctx, { from: 1567.98, duration: 0.7, type: "sine", gain: 0.06, delay: 0.3 });
    },
    remove(ctx) {
      tone(ctx, { from: 560, to: 280, duration: 0.2, type: "sine", gain: 0.12 });
      noise(ctx, { freq: 900, q: 0.8, duration: 0.05, gain: 0.05 });
    },
    error(ctx) {
      tone(ctx, { from: 330, duration: 0.12, type: "triangle", gain: 0.13 });
      tone(ctx, { from: 262, duration: 0.2, type: "triangle", gain: 0.13, delay: 0.12 });
    },
    notify(ctx) {
      tone(ctx, { from: 1318.5, duration: 0.5, type: "sine", gain: 0.13 });
      tone(ctx, { from: 1975.5, duration: 0.7, type: "sine", gain: 0.08, delay: 0.11 });
      tone(ctx, { from: 3951, duration: 0.3, type: "sine", gain: 0.012, delay: 0.11 });
    },
    tick(ctx) {
      noise(ctx, { freq: 5200, q: 2.5, duration: 0.006, gain: 0.14 });
    },
    /* soft "page turn" for carousels (cinema mode): a short airy brush and a gentle rising glide */
    slide(ctx) {
      noise(ctx, { freq: 1500, q: 0.7, duration: 0.07, gain: 0.07 });
      tone(ctx, { from: 520, to: 700, duration: 0.11, type: "sine", gain: 0.06, attack: 0.02 });
    },
    tada(ctx) {
      [392, 523.25, 659.25].forEach((frequency) => {
        tone(ctx, { from: frequency, duration: 0.14, type: "triangle", gain: 0.09 });
      });
      [523.25, 659.25, 783.99, 1046.5].forEach((frequency) => {
        tone(ctx, { from: frequency, duration: 0.75, type: "sine", gain: 0.08, delay: 0.15 });
      });
    },
  };

  /* Mechanical: keyboard-like clacks, thocks and a little desk bell. */
  const MECHANICAL = {
    click(ctx) {
      noise(ctx, { freq: 2200, q: 0.9, duration: 0.018, gain: 0.34 });
      noise(ctx, { freq: 5400, q: 3, duration: 0.006, gain: 0.14 });
    },
    nav(ctx) {
      noise(ctx, { freq: 1300, q: 1.1, duration: 0.03, gain: 0.3 });
      tone(ctx, { from: 190, to: 140, duration: 0.05, type: "sine", gain: 0.14 });
    },
    tap(ctx) {
      noise(ctx, { freq: 1800, q: 1, duration: 0.02, gain: 0.28 });
    },
    menu(ctx) {
      noise(ctx, { freq: 3000, q: 2, duration: 0.012, gain: 0.2 });
      noise(ctx, { freq: 2300, q: 2, duration: 0.012, gain: 0.16, delay: 0.035 });
    },
    switch(ctx) {
      noise(ctx, { freq: 2600, q: 1.2, duration: 0.015, gain: 0.3 });
      noise(ctx, { freq: 1500, q: 1.2, duration: 0.02, gain: 0.3, delay: 0.07 });
    },
    tick(ctx) {
      noise(ctx, { freq: 6000, q: 3, duration: 0.005, gain: 0.22 });
    },
    slide(ctx) {
      noise(ctx, { freq: 1600, q: 1, duration: 0.025, gain: 0.18 });
      tone(ctx, { from: 210, to: 170, duration: 0.05, type: "sine", gain: 0.08 });
    },
    success(ctx) {
      noise(ctx, { freq: 2000, q: 1, duration: 0.018, gain: 0.28 });
      tone(ctx, { from: 1200, duration: 0.08, type: "sine", gain: 0.08, delay: 0.02 });
      tone(ctx, { from: 1600, duration: 0.12, type: "sine", gain: 0.07, delay: 0.07 });
    },
    complete(ctx) {
      [0, 0.06, 0.12].forEach((delay) => noise(ctx, { freq: 2100, q: 1, duration: 0.016, gain: 0.26, delay: delay }));
      tone(ctx, { from: 2637, duration: 0.6, type: "sine", gain: 0.12, delay: 0.2 });
      tone(ctx, { from: 3951, duration: 0.35, type: "sine", gain: 0.035, delay: 0.2 });
    },
    remove(ctx) {
      noise(ctx, { freq: 900, q: 0.7, duration: 0.08, gain: 0.26 });
      tone(ctx, { from: 300, to: 150, duration: 0.12, type: "sine", gain: 0.08 });
    },
    error(ctx) {
      tone(ctx, { from: 120, duration: 0.1, type: "sine", gain: 0.3 });
      tone(ctx, { from: 110, duration: 0.14, type: "sine", gain: 0.3, delay: 0.12 });
      noise(ctx, { freq: 700, q: 1, duration: 0.03, gain: 0.15 });
    },
    notify(ctx) {
      tone(ctx, { from: 2093, duration: 0.45, type: "sine", gain: 0.13 });
      tone(ctx, { from: 2637, duration: 0.55, type: "sine", gain: 0.1, delay: 0.1 });
    },
    tada(ctx) {
      MECHANICAL.complete(ctx);
    },
  };

  /* Retro: small square-wave chiptune blips. */
  function chip(ctx, notes, options) {
    notes.forEach((frequency, index) => {
      tone(ctx, Object.assign({ from: frequency, duration: 0.06, type: "square", gain: 0.05, delay: index * 0.065 }, options || {}));
    });
  }

  const RETRO = {
    click(ctx) {
      tone(ctx, { from: 1200, duration: 0.025, type: "square", gain: 0.045 });
    },
    nav(ctx) {
      tone(ctx, { from: 660, to: 990, duration: 0.05, type: "square", gain: 0.045 });
    },
    tap(ctx) {
      tone(ctx, { from: 880, duration: 0.03, type: "square", gain: 0.045 });
    },
    menu(ctx) {
      chip(ctx, [520, 780], { duration: 0.035, gain: 0.04 });
    },
    switch(ctx) {
      chip(ctx, [523.25, 783.99], { duration: 0.05 });
    },
    tick(ctx) {
      tone(ctx, { from: 2000, duration: 0.015, type: "square", gain: 0.035 });
    },
    slide(ctx) {
      tone(ctx, { from: 440, to: 660, duration: 0.07, type: "square", gain: 0.035 });
    },
    success(ctx) {
      chip(ctx, [783.99, 1046.5, 1318.5]);
    },
    complete(ctx) {
      chip(ctx, [523.25, 659.25, 783.99, 1046.5]);
      tone(ctx, { from: 1318.5, duration: 0.35, type: "square", gain: 0.045, delay: 0.28 });
    },
    remove(ctx) {
      tone(ctx, { from: 440, to: 110, duration: 0.25, type: "square", gain: 0.05 });
    },
    error(ctx) {
      tone(ctx, { from: 196, duration: 0.12, type: "square", gain: 0.06 });
      tone(ctx, { from: 147, duration: 0.2, type: "square", gain: 0.06, delay: 0.12 });
    },
    notify(ctx) {
      chip(ctx, [1318.5, 1760], { type: "triangle", duration: 0.12, gain: 0.12, delay: 0 });
      tone(ctx, { from: 1760, duration: 0.15, type: "triangle", gain: 0.12, delay: 0.12 });
    },
    tada(ctx) {
      chip(ctx, [392, 523.25, 659.25, 783.99, 1046.5], { duration: 0.08 });
    },
  };

  const PACKS = { soft: SOFT, mechanical: MECHANICAL, retro: RETRO };

  function currentPack() {
    const pack = prefs.get("pack");
    return PACKS[pack] ? pack : "soft";
  }

  function playIn(pack, name) {
    const sounds = PACKS[pack] || SOFT;
    const fn = sounds[name] || SOFT[name];
    if (!fn) return;
    const ctx = audio();
    if (!ctx) return;
    playingUi = UI_SOUNDS.indexOf(name) !== -1;
    try {
      fn(ctx);
    } finally {
      playingUi = false;
    }
  }

  let lastPlayed = { name: "", at: 0 };

  const sound = {
    play(name) {
      if (!soundEnabled() || !SOFT[name]) return;
      const now = Date.now();
      // Avoid stacking the same short sound (e.g. click + htmx success in the same instant).
      if (lastPlayed.name === name && now - lastPlayed.at < 45) return;
      lastPlayed = { name: name, at: now };
      try {
        playIn(currentPack(), name);
      } catch (e) {
        /* audio is optional */
      }
    },
    enabled: soundEnabled,
    setEnabled(enabled, save) {
      root.dataset.sound = enabled ? "1" : "0";
      if (enabled) sound.play("success");
      if (save !== false) post("/tercihler/ses", { enabled: enabled ? "1" : "0" });
      window.dispatchEvent(new CustomEvent("efe-sound-changed", { detail: { enabled: enabled } }));
    },
    setVolume(value, save) {
      root.dataset.volume = String(value);
      if (save !== false) post("/tercihler/ses", { volume: String(value) });
    },
    names: Object.keys(SOFT),
    packs: Object.keys(PACKS),
    pack: currentPack,
    setPack(pack) {
      if (!PACKS[pack]) return;
      prefs.set("pack", pack === "soft" ? "" : pack);
      sound.play("success");
      window.dispatchEvent(new CustomEvent("efe-sound-changed", { detail: { pack: pack } }));
    },
    /* Play a short sample of a pack (preferences page), even if it is not selected. */
    preview(pack) {
      if (!soundEnabled()) return;
      try {
        playIn(pack, "click");
        window.setTimeout(() => playIn(pack, "success"), 160);
      } catch (e) {
        /* audio is optional */
      }
    },
    clickVolume() {
      return Math.round(clamp(prefs.get("clickVolume"), 60) * 100);
    },
    setClickVolume(value) {
      prefs.set("clickVolume", Math.max(0, Math.min(100, parseInt(value, 10) || 0)));
      sound.play("click");
    },
  };

  /* ------------------------------------------------------------------ */
  /* Confetti                                                           */
  /* ------------------------------------------------------------------ */
  function confetti(target) {
    const host = target || document.body;
    const rect = host.getBoundingClientRect();
    const layer = document.createElement("div");
    layer.style.cssText =
      "position:fixed;left:" + (rect.left + rect.width / 2) + "px;top:" + (rect.top + rect.height / 3) +
      "px;width:0;height:0;z-index:60;pointer-events:none";
    const colors = ["#6366f1", "#a855f7", "#f59e0b", "#10b981", "#ef4444", "#0ea5e9"];
    for (let index = 0; index < 36; index += 1) {
      const piece = document.createElement("span");
      piece.className = "ef-confetti-piece";
      const angle = Math.random() * Math.PI * 2;
      const distance = 90 + Math.random() * 180;
      piece.style.background = colors[index % colors.length];
      piece.style.setProperty("--dx", Math.cos(angle) * distance + "px");
      piece.style.setProperty("--dy", Math.sin(angle) * distance + 120 + "px");
      piece.style.setProperty("--rot", Math.random() * 720 - 360 + "deg");
      piece.style.animationDelay = Math.random() * 0.12 + "s";
      layer.appendChild(piece);
    }
    document.body.appendChild(layer);
    window.setTimeout(() => layer.remove(), 1600);
  }

  /* ------------------------------------------------------------------ */
  /* Notification center badge and browser notifications                */
  /* ------------------------------------------------------------------ */
  const FEED_URL = "/bildirimler/akis";
  const POLL_MS = 5 * 60 * 1000;
  let lastUnread = null;

  function updateBadges(count) {
    document.querySelectorAll("[data-efe-unread-badge]").forEach((badge) => {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.classList.toggle("hidden", !count);
    });
  }

  function browserNotificationsEnabled() {
    return storageGet("efe-browser-notifications", "0") === "1" && "Notification" in window &&
      Notification.permission === "granted";
  }

  function notifiedIds() {
    try {
      return JSON.parse(storageGet("efe-notified", "[]"));
    } catch (e) {
      return [];
    }
  }

  function showBrowserNotifications(entries) {
    if (!browserNotificationsEnabled()) return;
    const seen = notifiedIds();
    const fresh = entries.filter((entry) => entry.unread && seen.indexOf(entry.id) === -1).slice(0, 3);
    fresh.forEach((entry) => {
      const title = entry.released ? gettext("New release") : gettext("Coming soon");
      const body = entry.title + (entry.episode ? " · " + entry.episode : "") + " — " + entry.when;
      try {
        const notification = new Notification(title, { body: body, icon: entry.image, tag: entry.id });
        notification.onclick = () => {
          window.focus();
          window.location.href = entry.url;
        };
      } catch (e) {
        /* some browsers only allow notifications from a service worker */
      }
      seen.push(entry.id);
    });
    storageSet("efe-notified", JSON.stringify(seen.slice(-300)));
  }

  function refreshNotifications() {
    if (root.dataset.theme === "anon" || document.hidden) return;
    fetch(FEED_URL, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data) return;
        if (lastUnread !== null && data.unread > lastUnread) {
          sound.play("notify");
          document.querySelectorAll("[data-efe-bell]").forEach((bell) => {
            bell.classList.remove("ef-shake");
            void bell.offsetWidth;
            bell.classList.add("ef-shake");
          });
        }
        lastUnread = data.unread;
        updateBadges(data.unread);
        showBrowserNotifications(data.entries || []);
      })
      .catch(() => null);
  }

  const notifications = {
    refresh: refreshNotifications,
    updateBadges: updateBadges,
    browserEnabled: browserNotificationsEnabled,
    supported: () => "Notification" in window,
    permission: () => ("Notification" in window ? Notification.permission : "unsupported"),
    enableBrowser() {
      if (!("Notification" in window)) return Promise.resolve(false);
      return Notification.requestPermission().then((permission) => {
        const granted = permission === "granted";
        storageSet("efe-browser-notifications", granted ? "1" : "0");
        if (granted) {
          sound.play("notify");
          refreshNotifications();
        }
        return granted;
      });
    },
    disableBrowser() {
      storageSet("efe-browser-notifications", "0");
    },
  };

  /* ------------------------------------------------------------------ */
  /* Hooks into the existing app (htmx requests, clicks)                */
  /* ------------------------------------------------------------------ */
  function soundForRequest(event) {
    const detail = event.detail || {};
    const config = detail.requestConfig || {};
    const path = (config.path || (detail.pathInfo && detail.pathInfo.requestPath) || "").split("?")[0];
    if (!detail.successful) {
      if (detail.xhr && detail.xhr.status >= 400) sound.play("error");
      return;
    }
    if ((config.verb || "").toLowerCase() !== "post") return;
    const params = config.parameters || {};
    const status = typeof params.get === "function" ? params.get("status") : params.status;
    if (/delete/.test(path)) {
      sound.play("remove");
    } else if (/(media_save|episode_save|progress_edit|update_score|score)/.test(path)) {
      if (status === "Completed") {
        sound.play("complete");
      } else {
        sound.play("success");
      }
    } else if (/lists?_?|list_item/.test(path)) {
      sound.play("success");
    }
  }

  document.addEventListener("htmx:afterRequest", soundForRequest);

  const CLICKABLE = "a[href], button, [role=button], [role=menuitem], [role=tab], summary, label, select, " +
    "input[type=checkbox], input[type=radio], input[type=submit], input[type=button]";

  document.addEventListener("click", (event) => {
    if (!event.isTrusted || event.button !== 0) return;
    const element = event.target.closest("[data-efe-sound]");
    if (element) {
      sound.play(element.getAttribute("data-efe-sound"));
      return;
    }
    const clickable = event.target.closest(CLICKABLE);
    if (!clickable || clickable.closest("[data-efe-silent]") || clickable.disabled) return;
    // A label click also fires a click on its input; play only once.
    if (clickable.tagName === "INPUT" && clickable.labels && clickable.labels.length) return;
    sound.play(clickable.closest("aside") && clickable.matches("a[href]") ? "nav" : "click");
  }, true);

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form || form.getAttribute("hx-post") || form.getAttribute("hx-get")) return;
    const action = form.getAttribute("action") || "";
    if (/media_save|episode_save/.test(action)) {
      const status = form.querySelector("[name=status]");
      sound.play(status && status.value === "Completed" ? "complete" : "success");
    } else if (/delete/.test(action)) {
      sound.play("remove");
    }
  });

  document.addEventListener("efe-notifications-read", () => {
    lastUnread = 0;
    updateBadges(0);
  });

  window.addEventListener("load", () => {
    if (root.dataset.theme !== "anon") {
      refreshNotifications();
      window.setInterval(refreshNotifications, POLL_MS);
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) refreshNotifications();
      });
    }
  });

  /* Current appearance and page state, used by the macOS app's native menus. */
  function state() {
    const page = document.getElementById("efe-page-state");
    const data = page ? Object.assign({}, page.dataset) : {};
    return {
      theme: currentTheme(),
      tone: currentTone(),
      font: currentFont(),
      preset: currentPreset(),
      sound: soundEnabled(),
      scrolled: (window.scrollY || document.documentElement.scrollTop || 0) > 40,
      page: data,
      anonymous: root.dataset.theme === "anon",
      accent: currentAccent(),
      pack: currentPack(),
      sidebar: prefs.get("sidebar"),
    };
  }

  window.efe = {
    theme: theme,
    font: font,
    accent: accent,
    prefs: prefs,
    sound: sound,
    notifications: notifications,
    confetti: confetti,
    post: post,
    state: state,
  };

  // Let listeners (e.g. the macOS app) know the initial appearance.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => theme.apply());
  } else {
    theme.apply();
  }
})();
