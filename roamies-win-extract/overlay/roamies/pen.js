// The user's pen on Windows: while the talk keys are held, the voice helper follows the cursor and sends its trail
// here (voice "ink" messages, in global screen points; voice-win/src/ink.rs). Drawn like voice/ink.swift draws it
// on macOS: teal with a dark edge and a soft glow, dimmer until it snaps shut round something (then a glow pulse and
// a tick), and lines that never close fade. The picture sent with what was said is the helper's own capture, with
// the strokes drawn into it; this canvas is left out of captures like the rest of the overlay.
(() => {
  const R = window.Roamie;
  if (!R || typeof document === 'undefined') return;
  // The pen's colours and size, the same as ink.js and voice/ink.swift.
  const ACC = '#78ffe2', EDGE = 'rgba(3,39,34,0.6)', RADIUS = 2.8;
  const reducedMQ = matchMedia('(prefers-reduced-motion: reduce)');
  const loc = (x, y) => [x - (R.origin?.x ?? 0), y - (R.origin?.y ?? 0)];
  let cv = null, ctx = null, raf = 0, cur = null;
  const strokes = [];

  function canvas() {
    if (cv) return;
    cv = document.createElement('canvas');
    cv.setAttribute('aria-hidden', 'true');
    Object.assign(cv.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh', pointerEvents: 'none', zIndex: '2147483000' });
    document.body.appendChild(cv);
    ctx = cv.getContext('2d');
    const fit = () => { const d = devicePixelRatio || 1; cv.width = Math.round(innerWidth * d); cv.height = Math.round(innerHeight * d); paint(); };
    addEventListener('resize', fit);
    fit();
  }

  function path(pts) {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    if (pts.length === 1) { ctx.lineTo(pts[0][0] + 0.01, pts[0][1]); return; }
    // Through the midpoints, so the line stays smooth like the macOS pen.
    for (let i = 1; i < pts.length - 1; i++) ctx.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2);
    ctx.lineTo(pts.at(-1)[0], pts.at(-1)[1]);
  }

  function paint() {
    if (!ctx) return;
    const d = devicePixelRatio || 1, t = performance.now();
    ctx.setTransform(d, 0, 0, d, 0, 0);
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    ctx.lineCap = ctx.lineJoin = 'round';
    let moving = false;
    for (let i = strokes.length - 1; i >= 0; i--) {
      const s = strokes[i];
      let a = s.closed ? 1 : 0.55;
      if (s.fadeAt != null) {
        const k = (t - s.fadeAt) / s.fadeMs;
        if (k >= 1) { strokes.splice(i, 1); continue; }
        if (k > 0) a *= 1 - k;
        moving = true;
      }
      let glow = 6;
      if (s.pulseAt != null) {
        const k = (t - s.pulseAt) / 320;
        if (k < 1) { glow = 6 + 10 * (k < 0.5 ? k * 2 : 2 - k * 2); moving = true; } else s.pulseAt = null;
      }
      ctx.globalAlpha = a;
      path(s.pts);
      ctx.shadowBlur = 0;
      ctx.strokeStyle = EDGE; ctx.lineWidth = RADIUS * 2 + 2.5; ctx.stroke();
      ctx.shadowColor = 'rgba(120,255,226,0.75)'; ctx.shadowBlur = glow;
      ctx.strokeStyle = ACC; ctx.lineWidth = RADIUS * 2; ctx.stroke();
    }
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
    raf = moving ? requestAnimationFrame(paint) : 0;
  }
  const redraw = () => { if (!raf) raf = requestAnimationFrame(paint); };
  const fade = (s, ms, after = 0) => { s.fadeAt = performance.now() + after; s.fadeMs = reducedMQ.matches ? 120 : ms; };

  R.pen = (m) => {
    if (m.ev === 'start') { canvas(); cur = { pts: [loc(m.x, m.y)], closed: false }; strokes.push(cur); }
    else if (m.ev === 'pt' && cur) cur.pts.push(loc(m.x, m.y));
    else if (m.ev === 'snap' && cur) {
      // Closed into the loop that starts at `from`; what came before it (the run-up) goes.
      cur.pts = cur.pts.slice(m.from); cur.pts.push(cur.pts[0]);
      cur.closed = true;
      if (!reducedMQ.matches) cur.pulseAt = performance.now();
      R.sfx?.play('tick');
      cur = null;
    } else if (m.ev === 'drop' && cur) { fade(cur, 350); cur = null; }
    else if (m.ev === 'fade') { cur = null; for (const s of strokes) if (s.fadeAt == null) fade(s, 450, (+m.after || 0) * 1000); }
    redraw();
  };
})();
