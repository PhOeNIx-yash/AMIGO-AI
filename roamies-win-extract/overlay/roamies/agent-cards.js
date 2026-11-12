// Cards beside the Sidekick while it works, in the card look (glass.js) with the reminders card's header and marks:
//  - trail: the parts of a "do this, then that" request, ticking off as each finishes; a skipped part says why.
// API on agent.cards: trail(e)
(() => {
  const R = window.Roamie;
  const { clamp } = R;
  const G = R.glass;
  const BODY = `400 13px ${G.MONO}`, NOW = `500 13px ${G.MONO}`, SMALL = `400 11px ${G.MONO}`, HEAD = `500 10px ${G.MONO}`;
  const now = () => performance.now() / 1000;

  // ---------- a card beside the Sidekick: placed and sprung beside it, opening from its top-left corner ----------
  class Card {
    constructor() { this.open = false; this.age = 0; this.closing = 0; this.until = Infinity; this.x = { x: 0, v: 0 }; this.y = { x: 0, v: 0 }; this.placed = false; this.geo = null; }
    show(k) { if (!this.open || this.closing) { this.open = true; this.closing = 0; this.age = 0; this.placed = false; k.S('page'); } this.until = Infinity; }
    close(k, after = 0) { this.until = Math.min(this.until, now() + after); }
    fade() { return this.closing ? clamp(1 - this.closing / 0.3, 0, 1) : clamp(this.age / 0.22, 0, 1); }
    update(dt, k, side, slot) {
      if (!this.open) return;
      if (this.closing) { this.closing += dt; if (this.closing > 0.35) { this.open = false; this.closing = 0; } }
      else { this.age += dt; if (now() > this.until) { this.closing = 0.0001; k.S('poof'); } }
      const { w, h } = this.size(k), P = k.P, S = R.safe ?? { left: 0, top: 0, right: 0, bottom: 0 }, m = k.m;
      const l = S.left, t = S.top, r = innerWidth - S.right, b = innerHeight - S.bottom, gap = k.R * 1.35 + P * 3;
      // Beside the body, its bottom kept above the task tag that can open under it.
      let tx = side === 'right' ? m.x + gap : m.x - gap - w, ty = Math.min(m.y - h / 2 + slot, m.y + k.R * 1.2 - h);
      if (tx < l + 8 || tx + w > r - 8) { tx = m.x - w / 2; ty = m.y - k.R * 1.4 - h - P * 4 - Math.abs(slot); if (ty < t + 8) ty = m.y + k.R * 1.4 + P * 4 + Math.abs(slot); }
      tx = clamp(tx, l + 8, Math.max(l + 8, r - w - 8)); ty = clamp(ty, t + 8, Math.max(t + 8, b - h - 8));
      const spring = (s, target) => { s.v += ((target - s.x) * 70 - s.v * 11) * dt; s.x += s.v * dt; };
      if (!this.placed) { this.x.x = tx; this.y.x = ty; this.placed = true; }
      spring(this.x, tx); spring(this.y, ty);
      this.geo = { x: Math.round(this.x.x), y: Math.round(this.y.x), w, h };
    }
  }

  // ---------- trail: the parts of the request ----------
  class Trail extends Card {
    set(e, k) {
      const fresh = !this.open || this.closing;
      this.show(k);
      const before = this.steps ?? [];
      this.steps = (e.steps ?? []).map((s, i) => ({ ...s, at: before[i]?.state === s.state ? before[i].at : now() }));
      for (const [i, s] of this.steps.entries()) if (!fresh && before[i] && before[i].state !== s.state && (s.state === 'ok' || s.state === 'bad')) k.S(s.state === 'ok' ? 'tick' : 'error');
      this.head = String(e.head ?? 'your request').toLowerCase();
      this.lay = null;
      if (e.final) this.close(k, 4.5);
    }
    width() { return Math.round(clamp(innerWidth * 0.22, 260, 320)); }
    // Rows from the card's top-left: each step wraps to two lines; a skipped one says why underneath.
    layout() {
      const w = this.width();
      if (this.lay?.w === w) return this.lay;
      const tw = w - 40 - 16, rows = [];
      let y = 34;
      for (const [i, s] of (this.steps ?? []).entries()) {
        const lines = G.wrapRows(s.text, tw, s.state === 'now' ? NOW : BODY, 2), h = lines.length * 19 + 10;
        rows.push({ i, lines, y, h }); y += h;
        if (s.state === 'bad' && s.why) { const why = G.wrapRows(s.why, tw, SMALL, 3); rows.push({ i, why, y: y - 4, h: why.length * 16 + 4 }); y += why.length * 16 + 4; }
      }
      return (this.lay = { w, h: y + 10, rows });
    }
    size() { const L = this.layout(); return { w: L.w, h: L.h }; }
    box() { return this.open && this.geo ? [this.geo.x, this.geo.y, this.geo.x + this.geo.w, this.geo.y + this.geo.h] : null; }
    draw(ctx) {
      if (!this.open || !this.geo) return;
      const { x, y } = this.geo, L = this.layout(), w = L.w, t = now(), fade = this.fade();
      // Opens from its corner, like the reminders card.
      const e = 1 - (1 - clamp(this.age / 0.3, 0, 1)) ** 3, a = this.age - 0.3;
      ctx.save();
      ctx.globalAlpha *= fade;
      G.chrome(ctx, x, y, Math.max(16, w * e), Math.max(16, L.h * e));
      if (a > 0) {
        ctx.fillStyle = 'rgba(120,255,226,0.06)'; ctx.fillRect(x + 2, y + 2, w - 4, 24);
        ctx.fillStyle = G.DEEP; ctx.fillRect(x + 2, y + 26, w - 4, 1);
        [0, 1].forEach((k) => { ctx.fillStyle = k ? G.DEEP : G.ACC; ctx.fillRect(x + 12 + k * 12, y + 11, 5, 5); });
        G.typed(ctx, this.head, x + w / 2, y + 14, HEAD, G.DIM, a / 0.4, { anchor: 'middle' });
        const steps = this.steps ?? [], done = steps.filter((s) => s.state === 'ok' || s.state === 'bad').length;
        G.typed(ctx, `${done} of ${steps.length}`, x + w - 14, y + 14, HEAD, G.DIM, 1, { anchor: 'end' });
        ctx.save(); ctx.beginPath(); ctx.rect(x + 2, y + 27, w - 4, L.h - 29); ctx.clip();
        L.rows.forEach((r, n) => {
          const s = steps[r.i], ra = a - 0.12 - n * 0.06;
          if (!s || ra <= 0) return;
          const ry = y + r.y;
          if (r.why) { r.why.forEach((ln, j) => G.typed(ctx, ln, x + 40, ry + 8 + j * 16, SMALL, G.NOTE, (ra - 0.1) / 0.4)); return; }
          const since = t - s.at, ink = s.state === 'ok' ? G.MID : s.state === 'bad' ? G.CORAL : s.state === 'now' ? G.ACC : '#3f8f88';
          if (s.state === 'now') { ctx.fillStyle = 'rgba(120,255,226,0.07)'; ctx.fillRect(x + 4, ry, w - 8, r.h); }
          G.pen(ctx, G.ringPts(x + 24, ry + 12, 7, r.i * 2.3), ink, 2.2, ra / 0.3);
          const font = s.state === 'now' ? NOW : BODY;
          r.lines.forEach((ln, j) => G.typed(ctx, ln, x + 40, ry + 13 + j * 19, font, s.state === 'ok' || !s.state ? G.DIM : G.TEXT, (ra - 0.1) / 0.4));
          // Done: a tick in the ring, then the words struck through.
          if (s.state === 'ok') {
            const u = Math.min(ra, since);
            G.pen(ctx, [[x + 19.5, ry + 12], [x + 22.5, ry + 15.5], [x + 28.5, ry + 8]], G.HOT, 2.2, u / 0.2);
            r.lines.forEach((ln, j) => G.pen(ctx, [[x + 38, ry + 13 + j * 19], [x + 42 + G.textW(ln, font), ry + 12.5 + j * 19]], G.MID, 1.6, (u - 0.15 - j * 0.2) / 0.3));
          }
          if (s.state === 'bad') {
            const u = Math.min(ra, since);
            G.pen(ctx, [[x + 20.5, ry + 8], [x + 27.5, ry + 16]], G.CORAL, 2, u / 0.15);
            G.pen(ctx, [[x + 27.5, ry + 8], [x + 20.5, ry + 16]], G.CORAL, 2, (u - 0.1) / 0.15);
          }
          // The step being done: its ring pulses outward.
          if (s.state === 'now' && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
            const p = (t % 1.4) / 1.4;
            ctx.save(); ctx.globalAlpha *= 1 - p; ctx.strokeStyle = G.ACC; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x + 24, ry + 12, 8 + p * 9, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
          }
        });
        ctx.restore();
      }
      ctx.restore();
    }
  }

  const orig = R.agentPlugin;
  R.agentPlugin = (k) => {
    const p = orig(k);
    if (!p) return p;
    const trail = new Trail();
    const shown = () => k.presence === 'shown' || (k.presence === 'in' && k.presU > 0.72);
    const { over, update, quiet, extent } = p;
    p.quiet = () => !!quiet?.() && !trail.open;
    p.extent = () => {
      const e = extent?.(), t = shown() ? trail.box() : null;
      if (!t) return e ?? null;
      return e ? [Math.min(e[0], t[0]), Math.min(e[1], t[1]), Math.max(e[2], t[2]), Math.max(e[3], t[3])] : t;
    };
    p.over = (ctx, env) => { over(ctx, env); if (shown()) trail.draw(ctx); };
    p.update = (dt, env) => {
      update(dt, env);
      // The trail on the roomier side, away from a speech bubble beside it.
      const S = R.safe ?? { left: 0, right: 0 }, mid = (S.left + innerWidth - S.right) / 2;
      let side = k.m.x < mid ? 'right' : 'left';
      const bs = p.api?.bubble && !p.api.bubble.closing ? p.api.bubble.side : null;
      if (bs === side) side = side === 'right' ? 'left' : 'right';
      trail.update(dt, k, side, 0);
    };
    if (p.api) {
      const keep = () => p.api.show?.({ summoned: true });
      p.api.cards = {
        trail: (e) => { keep(); trail.set(e, k); },
        get open() { return trail.open; },
      };
    }
    return p;
  };
})();
