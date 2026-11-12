// The card look shared by the Sidekick's own UI (bubbles, the thinking tag, task tags, the step list): the same
// stepped corners, dark glass, teal edge, glint and Martian Mono as the drawings' cards (ink.js) and the reminders.
(() => {
  const R = window.Roamie;
  if (!R || typeof document === 'undefined') return;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const ACC = '#78ffe2', HOT = '#e6fffa', MID = '#28c4b8', DEEP = '#0e5c60', EDGE = 'rgba(3,39,34,0.6)';
  const GLASS = 'rgba(6,22,24,0.94)', TEXT = '#dcfff6', DIM = '#8ab8b0', CORAL = '#ff8a7a', NOTE = '#e9a196';
  const MONO = '"Martian Mono", ui-monospace, monospace';
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const rgba = (h, a) => `rgba(${hex(h).join(',')},${a})`;

  // The outline, clockwise from the top-left; tail: { side: 'top'|'right'|'bottom'|'left', at, to: [x, y] } adds a
  // stepped tail on that side, centred at `at` along it and stepping toward the point `to`.
  function stepped(x, y, w, h, c = 8, tail = null) {
    const s = c / 2, b = y + h, r = x + w;
    const pts = [[x + c, y], [r - c, y], [r - c, y + s], [r - s, y + s], [r - s, y + c], [r, y + c], [r, b - c], [r - s, b - c], [r - s, b - s], [r - c, b - s], [r - c, b],
      [x + c, b], [x + c, b - s], [x + s, b - s], [x + s, b - c], [x, b - c], [x, y + c], [x + s, y + c], [x + s, y + s], [x + c, y + s]];
    if (!tail) return pts;
    // Each side as the path walks it: where it starts, its direction, its outward normal, and the point it follows.
    const SIDE = { top: [[x, y], [1, 0], [0, -1], 0], right: [[r, y], [0, 1], [1, 0], 5], bottom: [[r, b], [-1, 0], [0, 1], 10], left: [[x, b], [0, -1], [-1, 0], 15] }[tail.side];
    if (!SIDE) return pts;
    const [o, e, n, after] = SIDE, len = Math.abs(e[0]) ? w : h;
    // Position along the walked side, kept clear of the corners.
    const w0 = tail.w ?? 12, along = clamp(Math.abs(e[0]) ? (tail.at - o[0]) * e[0] : (tail.at - o[1]) * e[1], c + w0 / 2 + 2, len - c - w0 / 2 - 2);
    const base = [o[0] + e[0] * along, o[1] + e[1] * along];
    const to = tail.to ?? [base[0] + n[0] * 14, base[1] + n[1] * 14];
    const depth = clamp((to[0] - base[0]) * n[0] + (to[1] - base[1]) * n[1], 6, tail.max ?? 18), shift = (to[0] - base[0]) * e[0] + (to[1] - base[1]) * e[1];
    const hs = depth / 3, ws = [w0, w0 * 0.66, w0 * 0.33], ds = [0, shift * 0.45, shift * 0.8];
    const P = (a, d) => [base[0] + e[0] * a + n[0] * d, base[1] + e[1] * a + n[1] * d];
    const t = [P(-ws[0] / 2, 0), P(-ws[0] / 2, hs), P(ds[1] - ws[1] / 2, hs), P(ds[1] - ws[1] / 2, 2 * hs), P(ds[2] - ws[2] / 2, 2 * hs), P(ds[2] - ws[2] / 2, 3 * hs),
      P(ds[2] + ws[2] / 2, 3 * hs), P(ds[2] + ws[2] / 2, 2 * hs), P(ds[1] + ws[1] / 2, 2 * hs), P(ds[1] + ws[1] / 2, hs), P(ws[0] / 2, hs), P(ws[0] / 2, 0)];
    pts.splice(after + 1, 0, ...t);
    return pts;
  }
  const steppedPath = (x, y, w, h, c, tail) => { const p = new Path2D(); stepped(x, y, w, h, c, tail).forEach((q, i) => (i ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1]))); p.closePath(); return p; };

  // Dark glass with a soft shadow, the accent edge with its glow, and a glint along the top.
  function chrome(c, x, y, w, h, o = {}) {
    const path = o.path || steppedPath(x, y, w, h, o.corner ?? 8, o.tail), edge = o.edge || ACC;
    c.save();
    c.fillStyle = GLASS; c.shadowColor = 'rgba(0,0,0,0.35)'; c.shadowBlur = 14; c.shadowOffsetY = 4; c.fill(path);
    c.shadowColor = rgba(edge, 0.7); c.shadowBlur = 8; c.shadowOffsetY = 0; c.strokeStyle = edge; c.lineWidth = 1.5; c.globalAlpha *= 0.9; c.stroke(path);
    if (o.glint !== false && w > 24) { c.globalAlpha *= 0.6; c.shadowBlur = 0; c.strokeStyle = '#ffffff'; c.lineWidth = 1; c.beginPath(); c.moveTo(x + 12, y + 1.5); c.lineTo(x + Math.min(w * 0.45, 90), y + 1.5); c.stroke(); }
    c.restore();
  }

  const meas = document.createElement('canvas').getContext('2d');
  const textW = (s, font) => { meas.font = font; return meas.measureText(String(s ?? '')).width; };
  // Words typing themselves in; the last couple land white. Returns the full width.
  function typed(c, str, x, y, font, color, u = 1, o = {}) {
    str = String(str ?? '');
    c.save(); c.font = font; c.textBaseline = o.baseline || 'middle'; c.textAlign = 'left';
    const full = c.measureText(str).width, x0 = o.anchor === 'middle' ? x - full / 2 : o.anchor === 'end' ? x - full : x;
    const n = Math.floor(str.length * clamp(u, 0, 1));
    if (n >= str.length) { c.fillStyle = color; c.fillText(str, x0, y); }
    else if (n > 0) { const head = str.slice(0, Math.max(0, n - 2)); c.fillStyle = color; c.fillText(head, x0, y); c.fillStyle = '#ffffff'; c.fillText(str.slice(head.length, n), x0 + c.measureText(head).width, y); }
    c.restore();
    return full;
  }
  // Lines of at most w px (a word wider than a line is broken); past max rows the last one ends in an ellipsis.
  function wrapRows(str, w, font, max = Infinity) {
    meas.font = font;
    const fits = (t) => meas.measureText(t).width <= w, rows = [];
    for (const para of String(str ?? '').split('\n')) {
      let row = '';
      for (let wd of para.split(/\s+/).filter(Boolean)) {
        const tr = row ? `${row} ${wd}` : wd;
        if (fits(tr)) { row = tr; continue; }
        if (row) { rows.push(row); row = ''; }
        while (!fits(wd)) { let k = wd.length - 1; while (k > 1 && !fits(wd.slice(0, k))) k--; rows.push(wd.slice(0, k)); wd = wd.slice(k); }
        row = wd;
      }
      rows.push(row);
    }
    if (rows.length > max) {
      const rest = rows.splice(max), full = `${rows[max - 1]} ${rest.join(' ')}`;
      let last = full;
      while (last && !fits(`${last}…`)) last = last.slice(0, -1);
      // End on a whole word when one is close, rather than mid-word.
      const sp = last.lastIndexOf(' ');
      if (full[last.length] !== ' ' && sp > last.length * 0.6) last = last.slice(0, sp);
      rows[max - 1] = `${last.trimEnd()}…`;
    }
    return rows;
  }
  // A hand-drawn ring: a slightly wobbly circle that overshoots where it closes, the same each time for one seed.
  const ringPts = (cx, cy, r, seed = 0) => Array.from({ length: 34 }, (_, i) => {
    const a = -2.2 + (i / 30) * Math.PI * 2, k = 1 + 0.06 * Math.sin(i * 1.7 + seed) + (i > 28 ? 0.08 : 0);
    return [cx + Math.cos(a) * r * k, cy + Math.sin(a) * r * k];
  });
  // The pen's stroke (a dark edge so it reads on white, then the colour with a soft glow), drawn up to u of its length.
  function pen(c, pts, color, w = 2.2, u = 1) {
    const n = Math.max(2, Math.ceil(pts.length * clamp(u, 0, 1)));
    if (u <= 0) return;
    const path = new Path2D(); pts.slice(0, n).forEach((q, i) => (i ? path.lineTo(q[0], q[1]) : path.moveTo(q[0], q[1])));
    c.save(); c.lineCap = 'round'; c.lineJoin = 'round';
    c.strokeStyle = EDGE; c.lineWidth = w + 2; c.stroke(path);
    c.shadowColor = rgba(color, 0.7); c.shadowBlur = 6; c.strokeStyle = color; c.lineWidth = w; c.stroke(path);
    c.restore();
  }

  // A pixel button like the drawings' chips (ink.js): filled is teal with dark ink, otherwise dark glass with a teal
  // edge and light ink. focus adds a bright ring, so the keyboard's choice shows; down presses it into its shadow.
  const CHIP_F = `700 11px ${MONO}`, INK_DARK = '#032722';
  const mix = (a, b, k) => `rgb(${hex(a).map((v, i) => Math.round(v + (hex(b)[i] - v) * k)).join(',')})`;
  const chipW = (label) => Math.round(textW(label, CHIP_F) + 22);
  function chip(c, x, y, w, h, label, o = {}) {
    c.save();
    if (o.focus) { c.strokeStyle = HOT; c.lineWidth = 2; c.stroke(steppedPath(x - 3.5, y - 3.5, w + 7, h + 9, 6)); }
    if (o.down) c.translate(0, 2);
    c.fillStyle = o.filled ? mix(ACC, '#000000', 0.62) : 'rgba(0,0,0,0.45)'; c.fill(steppedPath(x, y + (o.down ? 1 : 3), w, h, 4));
    const face = steppedPath(x, y, w, h, 4);
    if (o.filled) {
      c.fillStyle = o.hover ? mix(ACC, '#ffffff', 0.3) : ACC; c.fill(face);
      c.fillStyle = 'rgba(255,255,255,0.55)'; c.fillRect(x + 4, y + 1, w - 8, 1.5); c.fillStyle = 'rgba(3,39,34,0.25)'; c.fillRect(x + 4, y + h - 2.5, w - 8, 1.5);
    } else {
      c.fillStyle = GLASS; c.fill(face);
      if (o.hover) { c.fillStyle = rgba(ACC, 0.16); c.fill(face); }
      c.strokeStyle = ACC; c.lineWidth = 1.5; c.stroke(face);
    }
    c.font = CHIP_F; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = o.filled ? INK_DARK : TEXT;
    c.fillText(String(label ?? ''), x + w / 2, y + h / 2 + 0.5);
    c.restore();
  }

  R.glass = { ACC, HOT, MID, DEEP, EDGE, GLASS, TEXT, DIM, CORAL, NOTE, MONO, rgba, stepped, steppedPath, chrome, textW, typed, wrapRows, ringPts, pen, chip, chipW, CHIP_H: 23 };
})();
