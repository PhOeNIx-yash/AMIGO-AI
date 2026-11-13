// The mini agent: a small version of the Sidekick (the LED-matrix dither blob in its five-colour palette, ordered
// dither, 3x3 dots on a 4 px grid at 2x, pixel eyes) drawn on a small canvas, in the poses of a background agent's
// life: hatch, fly, work, wait, done, fail, stop. Also the pixel-art file icons of the "made" shelf and fileKind.
// API: Roamie.minis.draw(ctx, { size, dpr, pose, t, time, glyph, vx, vy, squash, reduce, seed }), .icon(kind),
//      .fileKind(name, dir), .KINDS
(() => {
  const R = window.Roamie;

  // ---------- which icon a file gets ----------
  const EXT = {
    doc: 'txt md markdown rtf doc docx odt pages', pdf: 'pdf', web: 'html htm', image: 'png jpg jpeg gif webp svg bmp tif tiff heic ico avif',
    sheet: 'csv tsv xls xlsx ods numbers', slides: 'ppt pptx key odp',
    code: 'js mjs cjs ts tsx jsx py rb go rs java c cc cpp h hpp cs css scss json yml yaml toml sh bash zsh swift kt php sql xml lua',
    model: 'stl obj glb gltf fbx 3mf usdz ply blend', audio: 'mp3 wav m4a aac flac ogg opus aiff aif', video: 'mp4 mov webm mkv avi m4v',
    archive: 'zip tar gz tgz bz2 xz 7z rar dmg',
  };
  const BY_EXT = new Map(Object.entries(EXT).flatMap(([k, list]) => list.split(' ').map((e) => [e, k])));
  const KINDS = ['folder', 'doc', 'pdf', 'web', 'image', 'sheet', 'slides', 'code', 'model', 'audio', 'video', 'archive', 'other'];
  const fileKind = (name, dir = false) => {
    const n = String(name ?? '');
    if (dir || n.endsWith('/')) return 'folder';
    const m = /\.([a-z0-9]+)$/i.exec(n);
    return (m && BY_EXT.get(m[1].toLowerCase())) || 'other';
  };

  // ---------- pixel-art file icons: 12x12 grids in the teal family ----------
  const INK = { k: '#051214', d: '#0e5c60', m: '#28c4b8', a: '#78ffe2', h: '#e6fffa' };
  const blank = () => Array.from({ length: 12 }, () => Array(12).fill('.'));
  const put = (g, cells, ch) => { for (const [r, c] of cells) if (g[r]?.[c] != null) g[r][c] = ch; };
  const row = (g, r, c0, c1, ch) => { for (let c = c0; c <= c1; c++) g[r][c] = ch; };
  const box = (g, r0, c0, r1, c1, fill) => {
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) g[r][c] = r === r0 || r === r1 || c === c0 || c === c1 ? 'a' : fill;
  };
  // A page with its corner folded over.
  const PAGE = ['.aaaaaaa....', '.adddddaa...', '.adddddama..', '.adddddaaaa.', ...Array(7).fill('.adddddddda.'), '.aaaaaaaaaa.'];
  const page = () => PAGE.map((r) => r.split(''));
  const GRIDS = {
    doc: () => { const g = page(); row(g, 2, 2, 5, 'h'); row(g, 5, 2, 8, 'm'); row(g, 7, 2, 9, 'm'); row(g, 9, 2, 6, 'm'); return g; },
    pdf: () => { const g = page(); row(g, 2, 2, 5, 'h'); row(g, 5, 2, 8, 'm'); for (let r = 7; r <= 9; r++) row(g, r, 2, 9, 'a'); put(g, [[8, 3], [8, 5], [8, 7]], 'k'); return g; },
    sheet: () => { const g = page(); row(g, 4, 2, 9, 'a'); for (const r of [6, 8]) row(g, r, 2, 9, 'm'); for (let r = 5; r <= 10; r++) put(g, [[r, 4], [r, 7]], 'm'); return g; },
    code: () => { const g = page(); put(g, [[6, 3], [7, 2], [8, 3], [6, 8], [7, 9], [8, 8], [5, 6], [6, 6], [7, 5], [8, 5], [9, 4]], 'h'); return g; },
    audio: () => { const g = page(); put(g, [[4, 7], [5, 7], [6, 7], [7, 7], [8, 7], [4, 8], [5, 9], [8, 5], [8, 6], [9, 4], [9, 5], [9, 6]], 'h'); return g; },
    video: () => { const g = page(); put(g, [[5, 4], [6, 4], [6, 5], [7, 4], [7, 5], [7, 6], [8, 4], [8, 5], [9, 4]], 'h'); return g; },
    archive: () => { const g = page(); for (let r = 1; r <= 7; r++) put(g, [[r, r % 2 ? 5 : 6]], 'h'); for (const r of [8, 9]) row(g, r, 5, 6, 'a'); return g; },
    other: () => { const g = page(); row(g, 5, 2, 8, 'm'); row(g, 7, 2, 7, 'm'); return g; },
    folder: () => { const g = blank(); box(g, 2, 0, 10, 11, 'd'); row(g, 1, 1, 4, 'a'); row(g, 4, 1, 10, 'm'); return g; },
    web: () => {
      const g = blank(); box(g, 1, 0, 10, 11, 'd'); row(g, 2, 1, 10, 'a'); put(g, [[2, 2], [2, 4], [2, 6]], 'k');
      put(g, [[4, 5], [4, 6], [5, 4], [5, 7], [6, 3], [6, 4], [6, 5], [6, 6], [6, 7], [6, 8], [7, 4], [7, 7], [8, 5], [8, 6]], 'h');
      put(g, [[5, 5], [5, 6], [7, 5], [7, 6]], 'm'); return g;
    },
    image: () => {
      const g = blank(); box(g, 1, 0, 10, 11, 'd'); put(g, [[3, 8], [3, 9], [4, 8], [4, 9]], 'h');
      put(g, [[5, 4], [6, 3], [6, 4], [6, 5], [6, 8]], 'a'); row(g, 7, 2, 6, 'm'); row(g, 7, 7, 9, 'm'); row(g, 8, 1, 10, 'm'); row(g, 9, 1, 10, 'm'); return g;
    },
    slides: () => {
      const g = blank(); box(g, 1, 0, 8, 11, 'd'); put(g, [[6, 2], [7, 2], [5, 4], [6, 4], [7, 4], [6, 8], [7, 8], [5, 8]], 'm');
      put(g, [[3, 6], [4, 6], [5, 6], [6, 6], [7, 6]], 'h'); row(g, 9, 5, 6, 'a'); row(g, 10, 3, 8, 'a'); return g;
    },
    model: () => ['............', '.....hh.....', '...hhhhhh...', '.hhhhhhhhhh.', '.aahhhhhhmm.', '.aaaahhmmmm.', '.aaaaammmmm.', '.aaaaammmmm.', '.aaaaammmmm.', '.aaaaammmmm.', '...aaammm...', '.....am.....'].map((r) => r.split('')),
  };
  const icons = new Map();
  // An inline SVG, one rect per lit cell (crisp at any scale). Decorative: the tile's button carries the name.
  const icon = (kind) => {
    if (icons.has(kind)) return icons.get(kind);
    const g = (GRIDS[kind] ?? GRIDS.other)();
    let rects = '';
    g.forEach((r, y) => r.forEach((ch, x) => { if (INK[ch]) rects += `<rect x="${x}" y="${y}" width="1" height="1" fill="${INK[ch]}"/>`; }));
    const svg = `<svg viewBox="0 0 12 12" width="36" height="36" shape-rendering="crispEdges" aria-hidden="true" focusable="false">${rects}</svg>`;
    icons.set(kind, svg);
    return svg;
  };

  // ---------- the mini agent ----------
  const PAL = [[4, 16, 18], [14, 92, 96], [40, 196, 184], [120, 255, 226], [230, 255, 250]].map((c) => `rgb(${c.join(',')})`);
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const ease = (u) => 1 - Math.pow(1 - clamp(u, 0, 1), 3);
  // Small glyphs (6x6, '#' lit) for what it's doing: writing, the web, a command, reading.
  const GLYPHS = {
    pen: ['....##', '...###', '..###.', '.###..', '##....', '#.....'],
    lens: ['.###..', '#...#.', '#...#.', '.###..', '....#.', '.....#'],
    gear: ['#.##.#', '.####.', '##..##', '##..##', '.####.', '#.##.#'],
    eye: ['......', '.####.', '#.##.#', '#.##.#', '.####.', '......'],
  };
  const ASK = ['.###.', '#...#', '....#', '..##.', '..#..', '.....', '..#..'];
  const bits = (ctx, rows, x, y, px, color) => {
    ctx.fillStyle = color;
    rows.forEach((r, j) => { for (let i = 0; i < r.length; i++) if (r[i] === '#') ctx.fillRect(Math.round(x + i * px), Math.round(y + j * px), px, px); });
  };

  // The dithered blob: an ellipse lit from the top left, pushed through bayer4 into the palette, dot by dot.
  // Coordinates in device pixels; rot turns the ellipse (flying along its path).
  function blob(ctx, cx, cy, r, sx, sy, rot, cell, alpha = 1) {
    const dot = cell - 1, cs = Math.cos(-rot), sn = Math.sin(-rot), reach = r * Math.max(sx, sy) + cell;
    ctx.globalAlpha = alpha;
    for (let gy = Math.floor((cy - reach) / cell); gy <= Math.ceil((cy + reach) / cell); gy++) {
      for (let gx = Math.floor((cx - reach) / cell); gx <= Math.ceil((cx + reach) / cell); gx++) {
        const px = gx * cell + cell / 2 - cx, py = gy * cell + cell / 2 - cy;
        const ux = (px * cs - py * sn) / (r * sx), uy = (px * sn + py * cs) / (r * sy), d = ux * ux + uy * uy;
        if (d > 1) continue;
        const lit = clamp(0.1 + 0.48 * Math.sqrt(1 - d) - 0.22 * ux - 0.3 * uy + (d > 0.78 ? -0.22 : 0), 0, 1);
        const level = clamp(Math.floor(lit * 3.999 + BAYER[(gy & 3) * 4 + (gx & 3)] - 0.5), 0, 3) + 1;
        ctx.fillStyle = PAL[level];
        ctx.fillRect(gx * cell, gy * cell, dot, dot);
      }
    }
    ctx.globalAlpha = 1;
  }
  function eyes(ctx, cx, cy, r, sx, sy, cell, open = 1, look = [0, 0]) {
    const h = open >= 1 ? 2 : 1;
    for (const s of [-1, 1]) {
      const ex = Math.round((cx + s * r * 0.34 * sx + look[0] * cell) / cell) * cell, ey = Math.round((cy - r * 0.12 * sy + look[1] * cell) / cell) * cell;
      // A lit patch round each eye, so it reads through the dither.
      ctx.fillStyle = PAL[3];
      for (let j = -1; j <= 2; j++) for (let i = -1; i <= 1; i++) ctx.fillRect(ex + i * cell, ey + j * cell, cell - 1, cell - 1);
      ctx.fillStyle = PAL[0];
      if (open > 0) ctx.fillRect(ex, ey + (h === 1 ? cell : 0), cell - 1, h * cell - 1);
    }
  }
  const blinkAt = (T, seed) => ((T + seed * 1.7) % 3.4) < 0.12;

  /** Draws one frame of a mini on a canvas of size x size CSS px. t: seconds in this pose; time: a shared clock. */
  function draw(ctx, { size = 32, dpr = 1, pose = 'work', t = 0, time = 0, glyph = null, vx = 0, vy = 0, squash = 0, reduce = false, seed = 0 } = {}) {
    const G = R.glass, S = size * dpr, cell = Math.max(3, Math.round(2 * dpr)), T = reduce ? 0 : time;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    let cx = S / 2, cy = S / 2 + 2 * dpr, r = S * 0.34, sx = 1, sy = 1, rot = 0, open = blinkAt(T, seed) ? 0.5 : 1, alpha = 1;
    const css = () => ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (pose === 'hatch') {
      const u = reduce ? 1 : clamp(t / 0.6, 0, 1);
      if (u < 1) {
        // Three dots gathering into an egg.
        for (let i = 0; i < 3; i++) {
          const a = (i * Math.PI * 2) / 3 + t * 6, d = (1 - ease(u)) * S * 0.32;
          blob(ctx, cx + Math.cos(a) * d, cy + Math.sin(a) * d, S * 0.09 + u * S * 0.08, 1, 1, 0, cell);
        }
        return;
      }
      const pulse = reduce ? 0 : Math.sin((t - 0.6) * 6) * 0.05;
      blob(ctx, cx, cy, r * (0.72 + pulse), 0.86, 1.1, 0, cell);
      return;
    }
    if (pose === 'fly') {
      const sp = Math.hypot(vx, vy), k = clamp(sp / 1600, 0, 0.35);
      sx = 1 + k - squash * 0.2; sy = 1 - k * 0.6 + squash * 0.25; rot = sp > 1 ? Math.atan2(vy, vx) : 0;
      if (squash < 0) { sx = 1 - squash * 0.35; sy = 1 + squash * 0.3; rot = 0; }
      blob(ctx, cx, cy, r, sx, sy, rot, cell);
      eyes(ctx, cx, cy, r, 1, 1, cell, 1, [clamp(vx / 600, -1, 1), clamp(vy / 600, -1, 1)]);
      return;
    }
    if (pose === 'work') {
      cy += Math.sin(T * 2.4 + seed) * 1.2 * dpr; sy = 1 + Math.sin(T * 4.8 + seed) * 0.03; sx = 2 - sy;
      blob(ctx, cx, cy, r, sx, sy, 0, cell); eyes(ctx, cx, cy, r, sx, sy, cell, open, [0.3, 0]);
      if (glyph && GLYPHS[glyph]) bits(ctx, GLYPHS[glyph], S - 7 * dpr, 0, Math.max(1, Math.round(dpr)), G?.HOT ?? '#e6fffa');
      return;
    }
    if (pose === 'wait') {
      const p = T % 2, hop = !reduce && p < 0.45 ? Math.sin((p / 0.45) * Math.PI) * 4 * dpr : 0;
      if (!reduce && p >= 0.45 && p < 0.6) { sy = 0.86; sx = 1.12; }
      blob(ctx, cx, cy - hop + (1 - sy) * r, r, sx, sy, 0, cell); eyes(ctx, cx, cy - hop, r, sx, sy, cell, 1, [0, -1]);
      // A pixel "?" in a small bubble, top right.
      const px = Math.max(1, Math.round(dpr)), bx = S - 8 * dpr, by = 0;
      ctx.fillStyle = 'rgba(5,18,20,0.95)'; ctx.fillRect(bx - px, by, 7 * px, 9 * px);
      bits(ctx, ASK, bx, by + px, px, G?.ACC ?? '#78ffe2');
      return;
    }
    if (pose === 'done') {
      const hop = reduce ? 0 : t < 0.5 ? Math.sin((t / 0.5) * Math.PI) * 5 * dpr : 0;
      if (!reduce && t >= 0.5 && t < 0.62) { sy = 0.86; sx = 1.12; }
      blob(ctx, cx, cy - hop, r, sx, sy, 0, cell); eyes(ctx, cx, cy - hop, r, sx, sy, cell, t > 0.3 && t < 0.9 ? 0.5 : 1, [0, -1]);
      css();
      // Sparkles, then the pen's tick (agent-cards.js draws the same mark).
      if (!reduce && t < 1.2) for (let i = 0; i < 4; i++) {
        const a = -Math.PI / 2 + (i - 1.5) * 0.9, d = 6 + t * 8, f = clamp(1 - Math.abs(t - 0.5) / 0.6, 0, 1);
        ctx.globalAlpha = f; ctx.fillStyle = i % 2 ? (G?.ACC ?? '#78ffe2') : (G?.HOT ?? '#e6fffa');
        const x = size / 2 + Math.cos(a) * (size * 0.3 + d) - 1, y = size / 2 - 4 + Math.sin(a) * (size * 0.3 + d) - 1;
        ctx.fillRect(x, y - 1, 1, 3); ctx.fillRect(x - 1, y, 3, 1);
      }
      ctx.globalAlpha = 1;
      if (G) G.pen(ctx, [[size - 10, 6], [size - 7.5, 8.5], [size - 3, 2.5]], G.HOT, 2, reduce ? 1 : clamp((t - 0.4) / 0.3, 0, 1));
      return;
    }
    if (pose === 'fail') {
      sx = 1.14; sy = 0.8; cy += 2 * dpr;
      blob(ctx, cx, cy, r, sx, sy, 0, cell, 0.9); eyes(ctx, cx, cy + cell, r, sx, sy, cell, 0.5, [0, 1]);
      css();
      if (G) { G.pen(ctx, [[size - 9, 2.5], [size - 3, 8.5]], G.CORAL, 2, reduce ? 1 : clamp(t / 0.15, 0, 1)); G.pen(ctx, [[size - 3, 2.5], [size - 9, 8.5]], G.CORAL, 2, reduce ? 1 : clamp((t - 0.1) / 0.15, 0, 1)); }
      return;
    }
    if (pose === 'stop') {
      const u = reduce ? 1 : clamp(t / 0.45, 0, 1);
      if (u < 0.7) blob(ctx, cx, cy, r * (1 - u / 0.7), 1, 1, 0, cell, 1 - u);
      // A poof of dots flying out.
      ctx.globalAlpha = 1 - u;
      for (let i = 0; i < 10; i++) {
        const a = (i / 10) * Math.PI * 2 + seed, d = (0.2 + ease(u) * 0.5) * S;
        ctx.fillStyle = PAL[3 + (i % 2)];
        ctx.fillRect(Math.round((cx + Math.cos(a) * d) / cell) * cell, Math.round((cy + Math.sin(a) * d) / cell) * cell, cell - 1, cell - 1);
      }
      ctx.globalAlpha = 1;
      return;
    }
    blob(ctx, cx, cy, r, 1, 1, 0, cell); eyes(ctx, cx, cy, r, 1, 1, cell, open);
  }

  R.minis = { draw, icon, fileKind, KINDS, GLYPHS: Object.keys(GLYPHS) };
})();
