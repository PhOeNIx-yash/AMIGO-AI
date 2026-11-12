// Tool-call performances for the sidekick: how long each tool plays, its sounds, and its face (mouth, brows and where
// it looks). What it's doing shows on the task tag under it (agent.js), so nothing else is drawn. The agent layer runs
// them via agent.tool().
(() => {
  const R = window.Roamie;
  const { clamp, lerp } = R;
  const T = (R.agentTools = {});
  const hash = (n) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

  // Looking at the screen (a screenshot): just the tag while the shot is taken. It stays up a moment so it can be read.
  T.look = {
    variants: ['screen', 'window'], label: 'looking at your screen',
    update(st) { if (st.age > 0.5 && !st.status) st.finish(true); },
    pose: () => null, // its usual face and pose: nothing to act out
  };

  // ===== SCREENSHOT =====  variants: screen | region | window
  // It aims (focus), scans, then the shutter goes. In live mode a window shot waits until the real window is located
  // (or a few seconds pass), then aims at exactly that window; it looks toward whatever it's shooting.
  const SHOT = { aim: 0.62, scan: 0.62, after: 1.0 };
  T.screenshot = {
    variants: ['screen', 'region', 'window'], label: 'screenshot', sound: 'focus',
    start(st) { st.d.aimAt = st.live && st.variant === 'window' && !st.target ? Infinity : 0.12; },
    retarget(st) { if (st.d.aimAt === Infinity) st.d.aimAt = st.age; },
    update(st, c) {
      const d = st.d;
      if (d.aimAt === Infinity) { if (st.age > 4 || (st.image && st.age > 1.2)) d.aimAt = st.age; return; }
      const t = st.age - d.aimAt;
      if (t > SHOT.aim * 0.78 && !d.locked) { d.locked = 1; c.S('focus'); }
      if (t > SHOT.aim && !d.scanning) { d.scanning = 1; c.S('scan'); }
      if (t >= SHOT.aim + SHOT.scan && !d.snapped) { d.snapped = st.age; c.S('shutter'); c.sq.kick(8); }
      if (d.snapped && st.age - d.snapped > SHOT.after) { c.S('gulp'); st.finish(true); }
    },
    pose(st, c) {
      const d = st.d, r = st.target, waiting = d.aimAt === Infinity;
      if (r) c.look((r[0] + r[2]) / 2, (r[1] + r[3]) / 2);
      return { mouth: d.snapped ? 'grin' : waiting ? 'o' : 'flat', brow: !waiting && !d.snapped ? 'determined' : waiting ? 'raised' : null };
    },
  };

  // ===== READ =====  variants: file | many | search
  T.read = {
    variants: ['file', 'many', 'search'], label: 'read',
    start(st) { st.d.page = 0; st.d.pages = st.variant === 'many' ? 3 + Math.floor(hash(st.seed) * 3) : 1; },
    update(st, c) {
      const d = st.d;
      if (st.variant === 'search') {
        d.found = d.found ?? 1.6 + hash(st.seed) * 0.8;
        if (st.age > d.found && !d.hit) { d.hit = 1; c.S('ping'); c.face.set('surprised', 0.4); }
        if (st.age > d.found + 1) st.finish(true);
        return;
      }
      const per = st.variant === 'many' ? 1.25 : 2.6, p = Math.floor(st.age / per);
      if (p !== d.page) {
        if (p >= d.pages) { st.finish(true); return; }
        d.page = p; c.S('page');
      }
      d.u = (((st.age % per) / per) * 6) % 1;
    },
    pose(st, c) {
      const d = st.d;
      if (st.variant === 'search') {
        // Its eyes sweep about, as if scanning for the match.
        c.look(c.bx + (0.1 + Math.sin(st.age * 2.6) * 0.55) * c.R * 2, c.by + (0.45 + Math.cos(st.age * 1.9) * 0.18 - (d.hit ? 0.25 : 0)) * c.R);
        return { mouth: d.hit ? 'o' : 'flat', brow: d.hit ? 'raised' : 'determined' };
      }
      c.look(c.bx + lerp(-0.4, 0.4, d.u ?? 0) * c.R, c.by + c.R);
      return { mouth: 'flat', brow: null };
    },
  };

  // ===== WRITE =====  variants: edit | create | delete (crumble)
  T.write = {
    variants: ['edit', 'create', 'delete'], label: 'write',
    start(st) { st.d.lines = 6; },
    update(st, c, dt) {
      const d = st.d;
      if (st.variant === 'delete' || st.variant === 'crumble') {
        d.crumble = clamp((st.age - 0.7) / 1.1, 0, 1);
        if (d.crumble > 0 && !d.cr) { d.cr = 1; c.S('crunch'); }
        if (st.age > 2.2) st.finish(true);
        return;
      }
      const t0 = st.variant === 'create' ? 0.7 : 0.2, w = Math.max(0, st.age - t0) / 0.42;
      d.wline = Math.floor(w);
      if (st.age > t0 && d.wline < d.lines && Math.random() < dt * 9) c.S('scribble');
      if (d.wline >= d.lines + 1) st.finish(true);
    },
    pose(st, c) {
      const d = st.d;
      c.look(c.bx, c.by + c.R);
      if (st.variant === 'delete' || st.variant === 'crumble') return { mouth: d.crumble ? 'frown' : 'o', brow: d.crumble ? 'raised' : null };
      return { mouth: st.variant === 'create' && st.age < 0.7 ? 'o' : 'flat', brow: 'determined' };
    },
  };

  // ===== RUN =====  variants: ok | fail | progress. The command types in, output runs, then it ends well or not.
  T.run = {
    variants: ['ok', 'progress', 'fail'], label: 'run',
    start(st) { st.d.typed = 0; st.d.cmdLen = 8 + Math.floor(hash(st.seed) * 6); },
    update(st, c) {
      const d = st.d;
      if (st.age < 0.9) {
        const n = Math.floor((st.age / 0.9) * d.cmdLen);
        if (n > d.typed) { d.typed = n; c.S('key', 0.8 + hash(n + st.seed) * 0.5); }
        return;
      }
      const outT = st.age - 0.9, total = st.variant === 'progress' ? 2.6 : 1.8;
      if (outT > total && !st.status) {
        if (st.variant === 'fail') { st.finish(false); c.face.set(null, 0); }
        else st.finish(true);
      }
    },
    pose(st, c) {
      const fail = st.status === 'fail';
      c.look(c.bx, c.by + c.R * 1.4); // down at its tag
      return { mouth: fail ? 'frown' : st.status === 'ok' ? 'grin' : 'flat', brow: fail ? 'angry' : 'determined' };
    },
  };

  // ===== WEB =====  variants: search | fetch
  T.web = {
    variants: ['search', 'fetch'], label: 'web',
    update(st, c, dt) {
      if (st.variant === 'fetch' && st.age > 0.4 && st.age < 2.4 && Math.random() < dt * 3) c.S('ping');
      if (st.variant === 'search' && Math.random() < dt * 3) c.S('tick');
      if (st.age > (st.variant === 'fetch' ? 2.9 : 2.4)) st.finish(true);
    },
    pose(st, c) {
      c.look(c.bx + c.R * 0.8, c.by - c.R * 1.4);
      return { mouth: 'o', brow: 'raised' };
    },
  };
})();
