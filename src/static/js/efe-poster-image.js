// efeTrack: "Resim olarak kaydet" — one picture of a poster grid (Planlananlar and Poster Duvarı).
// Both pages call efePosterImage.save(...) so they produce the same kind of picture:
// a header (efeTrack · title · count), posters with rounded corners, and under each poster
// its name (and, when several types are shown together, its type: "Twin Peaks / Dizi").
(function () {
  "use strict";

  const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  const BG = "#0E1116";
  const CARD = "#1a1d24";
  const FG = "#e5e7eb";
  const MUTED = "#9ca3af";
  const ACCENT = "#818cf8";
  const MAX_PIXELS = 16000000; // iPhone Safari refuses bigger canvases

  function source(url, proxy, big) {
    if (!url) return null;
    // TMDB allows drawing its images on a canvas; other covers come through efeTrack.
    if (url.includes("image.tmdb.org/t/p/")) return url.replace(/\/t\/p\/[^/]+\//, big ? "/t/p/w342/" : "/t/p/w185/");
    if (url.includes("?")) return null; // the proxy refuses addresses with a query string
    return proxy + encodeURIComponent(url);
  }

  function load(src) {
    return new Promise((resolve) => {
      if (!src) { resolve(null); return; }
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  function lines(ctx, text, width, max) {
    const words = (text || "").split(/\s+/).filter(Boolean);
    const out = [];
    let line = "";
    for (const word of words) {
      const trial = line ? line + " " + word : word;
      if (ctx.measureText(trial).width <= width || !line) { line = trial; } else { out.push(line); line = word; }
    }
    if (line) out.push(line);
    if (out.length > max) {
      out.length = max;
      out[max - 1] += "…";
    }
    return out.map((l) => {
      if (ctx.measureText(l).width <= width) return l;
      let base = l.replace(/…$/, "");
      while (base.length > 1 && ctx.measureText(base + "…").width > width) base = base.slice(0, -1);
      return base + "…";
    });
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function download(canvas, fileName) {
    return new Promise((resolve) => {
      canvas.toBlob((blob) => {
        if (!blob) { resolve(false); return; }
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 60000);
        resolve(true);
      }, "image/jpeg", 0.92);
    });
  }

  /**
   * cards: [{ title, poster, kind }] (kind: "Dizi", "Film"… or "")
   * options: { title, subtitle, showKind, fileName, proxy, onProgress(done, total) }
   * Returns true when the picture was made.
   */
  async function save(cards, options) {
    if (!cards.length) return false;
    const showKind = !!options.showKind;
    // Layout in base units; everything is multiplied by `scale` for a sharp picture.
    const w = 130, h = 195, gapX = 14, gapY = 18, pad = 28, header = 104, footer = 40;
    const label = showKind ? 58 : 42;
    const cellW = w + gapX, cellH = h + label + gapY;
    const cols = Math.max(4, Math.min(cards.length, Math.round(Math.sqrt(cards.length * (cellH / cellW) * (16 / 9)))));
    const rows = Math.ceil(cards.length / cols);
    const width = pad * 2 + cols * cellW - gapX;
    const height = header + rows * cellH - gapY + footer;
    const scale = Math.max(1, Math.min(2, Math.sqrt(MAX_PIXELS / (width * height))));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext("2d");
    ctx.scale(scale, scale);
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, width, height);

    // Header: brand, title, count — the same on both pages
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    ctx.fillStyle = ACCENT;
    ctx.font = "700 15px " + FONT;
    ctx.fillText("efeTrack", pad, 38);
    ctx.fillStyle = FG;
    ctx.font = "800 30px " + FONT;
    ctx.fillText(options.title || "", pad, 74);
    if (options.subtitle) {
      const titleWidth = ctx.measureText(options.title || "").width;
      ctx.fillStyle = MUTED;
      ctx.font = "500 16px " + FONT;
      ctx.fillText(options.subtitle, pad + titleWidth + 14, 74);
    }
    ctx.fillStyle = "#262b35";
    ctx.fillRect(pad, header - 16, width - pad * 2, 1);

    const big = scale > 1.4;
    let next = 0, done = 0;
    const worker = async () => {
      while (next < cards.length) {
        const index = next++;
        const img = await load(source(cards[index].poster, options.proxy, big));
        const x = pad + (index % cols) * cellW, y = header + Math.floor(index / cols) * cellH;
        ctx.save();
        roundRect(ctx, x, y, w, h, 8);
        ctx.clip();
        if (img) {
          const fit = Math.max(w / img.width, h / img.height);
          const sw = w / fit, sh = h / fit;
          ctx.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
        } else {
          ctx.fillStyle = CARD;
          ctx.fillRect(x, y, w, h);
          ctx.fillStyle = MUTED;
          ctx.font = "600 13px " + FONT;
          ctx.textAlign = "center";
          lines(ctx, cards[index].title, w - 16, 4).forEach((l, n) => ctx.fillText(l, x + w / 2, y + h / 2 - 20 + n * 17));
          ctx.textAlign = "left";
        }
        ctx.restore();
        done++;
        if (options.onProgress) options.onProgress(done, cards.length);
      }
    };
    await Promise.all(Array.from({ length: 8 }, worker));

    // Names (and types) under the posters
    cards.forEach((card, index) => {
      const x = pad + (index % cols) * cellW, y = header + Math.floor(index / cols) * cellH + h + 17;
      ctx.fillStyle = FG;
      ctx.font = "600 12.5px " + FONT;
      const titleLines = lines(ctx, card.title, w, 2);
      titleLines.forEach((l, n) => ctx.fillText(l, x, y + n * 15.5));
      if (showKind && card.kind) {
        ctx.fillStyle = ACCENT;
        ctx.font = "600 11px " + FONT;
        ctx.fillText(card.kind, x, y + titleLines.length * 15.5 + 2);
      }
    });

    ctx.fillStyle = "#4b5263";
    ctx.font = "500 12px " + FONT;
    ctx.textAlign = "right";
    const today = new Date().toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
    ctx.fillText("efeTrack · " + today, width - pad, height - 16);
    ctx.textAlign = "left";

    return download(canvas, options.fileName || "efeTrack.jpg");
  }

  // MARK: collage of a fixed size ("Bu ay izlediklerim": story, post, wallpaper…)

  const imageCache = new Map();
  function loadCached(src) {
    if (!src) return Promise.resolve(null);
    if (!imageCache.has(src)) imageCache.set(src, load(src));
    return imageCache.get(src);
  }

  // Largest poster size that fits n posters (2:3, with a name area) into the area.
  function fit(n, aw, ah, labelRatio) {
    let best = { cols: 1, rows: n, w: 0 };
    for (let cols = 1; cols <= n; cols += 1) {
      const rows = Math.ceil(n / cols);
      const gapRatio = 0.09;
      const w = Math.min(aw / (cols + (cols - 1) * gapRatio), ah / (rows * (1.5 + labelRatio) + (rows - 1) * gapRatio));
      if (w > best.w) best = { cols: cols, rows: rows, w: w };
    }
    return best;
  }

  /**
   * cards: [{ title, poster, kind }]
   * options: { width, height, title, subtitle, showNames, showKind, proxy, canvas? }
   * Draws into options.canvas (or a new one) and returns it.
   */
  async function collage(cards, options) {
    const W = options.width, H = options.height;
    const canvas = options.canvas || document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d");
    const unit = Math.min(W, H);
    const pad = Math.round(unit * 0.06);
    const showNames = options.showNames !== false && cards.length > 0;
    const showKind = showNames && !!options.showKind;

    // Background: the app's dark page with a faint indigo glow at the top
    ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
    const glow = ctx.createRadialGradient(W * 0.2, 0, 0, W * 0.2, 0, Math.max(W, H) * 0.7);
    glow.addColorStop(0, "rgba(99, 102, 241, 0.16)"); glow.addColorStop(1, "rgba(99, 102, 241, 0)");
    ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);

    // Header
    const brand = Math.round(unit * 0.03), big = Math.round(unit * 0.07), small = Math.round(unit * 0.03);
    ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
    ctx.fillStyle = ACCENT; ctx.font = "700 " + brand + "px " + FONT;
    ctx.fillText("efeTrack", pad, pad + brand);
    ctx.fillStyle = FG; ctx.font = "800 " + big + "px " + FONT;
    ctx.fillText(options.title || "", pad, pad + brand + big * 1.15);
    ctx.fillStyle = MUTED; ctx.font = "500 " + small + "px " + FONT;
    ctx.fillText(options.subtitle || "", pad, pad + brand + big * 1.15 + small * 1.6);
    const top = pad + brand + big * 1.15 + small * 1.6 + pad * 0.8;
    const footer = Math.round(unit * 0.05);

    if (!cards.length) {
      ctx.fillStyle = MUTED; ctx.textAlign = "center"; ctx.font = "500 " + small + "px " + FONT;
      ctx.fillText("Bu ay izlediğin bir şey yok", W / 2, H / 2);
      return canvas;
    }

    const labelRatio = showNames ? (showKind ? 0.42 : 0.3) : 0;
    const aw = W - pad * 2, ah = H - top - footer - pad * 0.6;
    const grid = fit(cards.length, aw, ah, labelRatio);
    const w = grid.w, h = w * 1.5, gap = w * 0.09, cellH = h + w * labelRatio + gap;
    const gridW = grid.cols * w + (grid.cols - 1) * gap;
    const gridH = grid.rows * cellH - gap;
    const left = (W - gridW) / 2, startY = top + Math.max(0, (ah - gridH) / 2);
    const big2 = w > 200;
    const images = await Promise.all(cards.map((card) => loadCached(source(card.poster, options.proxy, big2))));
    const nameSize = Math.max(10, w * 0.095), kindSize = Math.max(9, w * 0.08);
    cards.forEach((card, index) => {
      const col = index % grid.cols, row = Math.floor(index / grid.cols);
      // Last row centred when it is not full
      const inRow = row === grid.rows - 1 ? cards.length - row * grid.cols : grid.cols;
      const rowLeft = left + (gridW - (inRow * w + (inRow - 1) * gap)) / 2;
      const x = rowLeft + col * (w + gap), y = startY + row * cellH;
      ctx.save();
      ctx.shadowColor = "rgba(0, 0, 0, .45)"; ctx.shadowBlur = w * 0.08; ctx.shadowOffsetY = w * 0.03;
      roundRect(ctx, x, y, w, h, w * 0.06); ctx.fillStyle = CARD; ctx.fill();
      ctx.restore();
      ctx.save();
      roundRect(ctx, x, y, w, h, w * 0.06); ctx.clip();
      const img = images[index];
      if (img) {
        const s = Math.max(w / img.width, h / img.height), sw = w / s, sh = h / s;
        ctx.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
      } else {
        ctx.fillStyle = MUTED; ctx.textAlign = "center"; ctx.font = "600 " + nameSize + "px " + FONT;
        lines(ctx, card.title, w - w * 0.12, 4).forEach((l, n) => ctx.fillText(l, x + w / 2, y + h / 2 + n * nameSize * 1.25));
        ctx.textAlign = "left";
      }
      ctx.restore();
      if (showNames) {
        ctx.fillStyle = FG; ctx.font = "600 " + nameSize + "px " + FONT;
        const names = lines(ctx, card.title, w, 2);
        names.forEach((l, n) => ctx.fillText(l, x, y + h + nameSize * 1.35 + n * nameSize * 1.22));
        if (showKind && card.kind) {
          ctx.fillStyle = ACCENT; ctx.font = "600 " + kindSize + "px " + FONT;
          ctx.fillText(card.kind, x, y + h + nameSize * 1.35 + names.length * nameSize * 1.22 + kindSize * 0.15);
        }
      }
    });

    ctx.fillStyle = "#4b5263"; ctx.font = "500 " + Math.round(unit * 0.022) + "px " + FONT; ctx.textAlign = "center";
    ctx.fillText("efeTrack", W / 2, H - footer * 0.55);
    ctx.textAlign = "left";
    return canvas;
  }

  window.efePosterImage = { save: save, collage: collage, download: download };
})();
