// The agents' home: a small panel in the top-right corner of the overlay with a row for each background agent
// (its mini, what it's doing, how long, Stop), Allow / Don't allow when one waits for a yes, and, when one finishes,
// a "made" shelf under it with what it made as tiles that open with one click. Same frame, glass and type as the
// research card (agent-panel.js) and the ask field (ask-bar.js). Minis are drawn by minis.js.
// The overlay is click-through, so the host asks Roamie.homeHit(x, y) (and on Linux Roamie.homeRects()).
// API: Roamie.agentHome.event(e, { from }) .hatch([x, y]) .hatchOff() .send (set by host.js)
(() => {
  const R = window.Roamie;
  const INSET = 12;
  // Its top-left for its size [w, h] inside the safe area { top, right, bottom, left } of a [w, h] viewport.
  R.agentHomePlace = (size, safe, view) => {
    const s = safe ?? { top: 0, right: 0, bottom: 0, left: 0 };
    return [Math.round(Math.max(s.left + INSET, view[0] - s.right - INSET - size[0])), Math.round(s.top + INSET)];
  };
  // The made panel's open button: where it worked (a folder, a file, a page), else its files; null for neither.
  R.agentOpenLabel = (result) => {
    const kind = result?.place?.kind;
    if (kind) return { folder: 'Open folder', file: 'Open file', url: 'Open page' }[kind] ?? null;
    return result?.files?.length ? 'Open folder' : null;
  };
  if (typeof document === 'undefined') return;

  const css = `
.ah-col { position: fixed; left: 0; top: 0; z-index: 5; width: 300px; display: flex; flex-direction: column; gap: 8px; pointer-events: none; will-change: transform; }
.ah, .ahs { --ink: #e8fffa; --ink-2: #b4d8d1; --ink-3: #8ab8b0; --accent: #78ffe2; --line-hi: #2f7c74; --line-hot: #4fbfa9; --note: #e9a196;
  --s: 2px; --ease: cubic-bezier(.2, .8, .2, 1);
  --notch: polygon(
    calc(var(--s) * 2) 0, calc(100% - var(--s) * 2) 0, calc(100% - var(--s) * 2) var(--s), calc(100% - var(--s)) var(--s),
    calc(100% - var(--s)) calc(var(--s) * 2), 100% calc(var(--s) * 2), 100% calc(100% - var(--s) * 2),
    calc(100% - var(--s)) calc(100% - var(--s) * 2), calc(100% - var(--s)) calc(100% - var(--s)),
    calc(100% - var(--s) * 2) calc(100% - var(--s)), calc(100% - var(--s) * 2) 100%, calc(var(--s) * 2) 100%,
    calc(var(--s) * 2) calc(100% - var(--s)), var(--s) calc(100% - var(--s)), var(--s) calc(100% - var(--s) * 2),
    0 calc(100% - var(--s) * 2), 0 calc(var(--s) * 2), var(--s) calc(var(--s) * 2), var(--s) var(--s), calc(var(--s) * 2) var(--s));
  pointer-events: auto; cursor: default; box-sizing: border-box; padding: 2px; background: var(--line-hot); color: #051214; clip-path: var(--notch);
  font: 400 12px/1.45 "Martian Mono", ui-monospace, Menlo, monospace;
  filter: drop-shadow(0 0 10px rgba(120, 255, 226, .18)) drop-shadow(0 8px 22px rgba(0, 0, 0, .45)); }
.ah-in, .ahs-in { background: rgba(5, 18, 20, .97); color: var(--ink); clip-path: var(--notch); }
/* Panels unfold on the way in and only dissolve on the way out: the entrance is a one-way keyframe, the exit
   is just the opacity transition (260 ms). */
.ah { transform-origin: top right; opacity: 0; transition: opacity .26s ease-out; }
.ah.on { opacity: 1; transition-duration: .22s; animation: ah-unfold .22s var(--ease); }
@keyframes ah-unfold { from { transform: scale(.9); } }
.ah.gone { display: none; }
.ah-head { display: flex; align-items: center; gap: 8px; padding: 9px 12px 8px 14px; border-bottom: 1px solid #12302e; }
.ah-dot { width: 6px; height: 6px; flex: none; background: var(--accent); box-shadow: 0 0 6px var(--accent); }
.ah-count { flex: 1; min-height: 1.3em; font-size: 11px; line-height: 1.3; text-transform: uppercase; color: var(--ink-3); white-space: nowrap; }
.ah-rows { list-style: none; margin: 0; padding: 0; }
/* Rows open from nothing (180 ms). Leaving, a row dissolves first (.fade, 260 ms), then the gap closes. */
.ah-row { display: grid; grid-template-rows: 1fr; opacity: 1; transition: grid-template-rows .18s var(--ease), opacity .18s var(--ease); }
.ah-row.shut { grid-template-rows: 0fr; opacity: 0; }
.ah-row.fade { opacity: 0; transition: opacity .26s ease-out; }
.ah-row.fade.shut { transition: grid-template-rows .18s var(--ease); }
.ah-row-in { min-height: 0; overflow: hidden; }
.ah-row + .ah-row .ah-row-in { border-top: 1px solid #0f2725; }
.ah-line { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; align-items: center; column-gap: 10px; padding: 8px 6px 8px 12px; }
/* desktop.html pins every canvas (position: fixed, grab cursor): keep the minis in their row's flow. */
.ah-mini, .ahs-mini { position: static; pointer-events: none; will-change: auto; }
.ah-mini { width: 32px; height: 32px; display: block; }
.ah-text { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.ah-title { font-size: 13px; line-height: 1.35; font-weight: 500; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-status { font-size: 11px; line-height: 1.35; color: var(--ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-row[data-state="failed"] .ah-status { color: var(--note); }
.ah-x, .ahs-x { all: unset; box-sizing: border-box; width: 26px; height: 26px; display: grid; place-items: center; color: var(--ink-3); cursor: pointer; flex: none; transition: color .12s var(--ease), background-color .12s var(--ease); }
.ah-x:hover, .ahs-x:hover { color: var(--ink); background: #0d2a2a; }
.ah-x:active, .ahs-x:active { color: var(--ink); background: #12302e; }
.ah-x:focus-visible, .ahs-x:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.ah-x:disabled { color: var(--line-hi); background: transparent; cursor: default; }
/* Stop asks first, in the same spot (like the panel's X buttons): pressed, it turns into Confirm for 4 s. */
.ah-x { min-width: 26px; width: auto; }
.ah-x > .yes { display: none; }
.ah-x[aria-pressed="true"] { padding: 0 8px; background: #ffb4a8; color: #032722; font: 700 11px/1.25 "Martian Mono", ui-monospace, Menlo, monospace; animation: ah-arm 600ms steps(2, end) 2; }
.ah-x[aria-pressed="true"]:hover { background: #ffcbc2; color: #032722; }
.ah-x[aria-pressed="true"] > .no { display: none; }
.ah-x[aria-pressed="true"] > .yes { display: inline; }
@keyframes ah-arm { 50% { background: #ff8a78; } }
/* Waiting for a yes: the question and two buttons, under the row's text. */
/* The question opens like a row (height, then the words fade in) and closes the other way round. */
.ah-ask { display: grid; grid-template-rows: 0fr; opacity: 0; visibility: hidden;
  transition: opacity .2s ease-out, grid-template-rows .18s var(--ease) .2s, visibility 0s linear .38s; }
.ah-ask.open { grid-template-rows: 1fr; opacity: 1; visibility: visible;
  transition: grid-template-rows .18s var(--ease), opacity .2s var(--ease) .12s, visibility 0s; }
.ah-ask-in { min-height: 0; overflow: hidden; }
.ah-ask-pad { padding: 0 12px 10px 54px; }
.ah-q { margin: 0 0 8px; font-size: 11.5px; line-height: 1.45; color: var(--ink-2); overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ah-btns { display: flex; gap: 8px; }
.ah-chip, .ahs-chip { all: unset; box-sizing: border-box; height: 26px; padding: 0 11px; display: inline-grid; place-items: center; cursor: pointer;
  font: 700 11px/1.25 "Martian Mono", ui-monospace, Menlo, monospace; white-space: nowrap;
  --c: 4px; clip-path: polygon(var(--c) 0, calc(100% - var(--c)) 0, 100% var(--c), 100% calc(100% - var(--c)), calc(100% - var(--c)) 100%, var(--c) 100%, 0 calc(100% - var(--c)), 0 var(--c));
  background: #0b2224; color: var(--ink); box-shadow: inset 0 0 0 1.5px var(--accent); transition: background-color .12s var(--ease), color .12s var(--ease); }
.ah-chip:hover, .ahs-chip:hover { background: #12393a; color: var(--ink); }
.ah-chip:active, .ahs-chip:active { background: #164446; color: var(--ink); }
.ah-chip.yes { background: var(--accent); color: #032722; box-shadow: none; }
.ah-chip.yes:hover { background: #a6ffeb; color: #032722; }
.ah-chip.yes:active { background: #5fe0c6; color: #032722; }
.ah-chip:focus-visible, .ahs-chip:focus-visible { outline: 2px solid #e6fffa; outline-offset: 2px; clip-path: none; }
.ah-chip:disabled, .ahs-chip:disabled { background: #0b2224; color: var(--ink-3); box-shadow: inset 0 0 0 1.5px var(--line-hi); cursor: default; }
/* The made shelf: what one agent made, until it's closed. */
.ahs { opacity: 0; transition: opacity .26s ease-out; }
.ahs.on { opacity: 1; transition-duration: .22s; animation: ahs-in .22s var(--ease); }
@keyframes ahs-in { from { transform: translateX(16px); } }
.ahs-head { display: flex; align-items: center; gap: 8px; padding: 6px 6px 4px 10px; }
.ahs-mini { width: 24px; height: 24px; flex: none; display: block; }
.ahs-title { flex: 1; min-width: 0; font-size: 13px; line-height: 1.35; font-weight: 500; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ahs-out, .ahs-need { margin: 2px 0 0; padding: 0 14px; font-size: 11px; line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ahs-out { color: var(--ink-2); }
.ahs-need { color: var(--note); }
.ahs-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; padding: 10px 8px 6px; }
.ahs-tile { all: unset; box-sizing: border-box; min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 8px 2px 6px; cursor: pointer;
  background: transparent; color: var(--ink-2); transition: background-color .12s var(--ease), color .12s var(--ease); }
.ahs-tile:hover { background: #0d2a2a; color: var(--ink); }
.ahs-tile:active { background: #12302e; color: var(--ink); }
.ahs-tile:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.ahs-tile:disabled { color: var(--ink-3); cursor: default; }
.ahs-tile svg { width: 36px; height: 36px; display: block; }
/* One line, never broken inside a word: a long name is shortened in the middle, keeping its extension (fitName). */
.ahs-name { max-width: 100%; font-size: 11px; line-height: 1.3; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ahs-more { width: 36px; height: 36px; display: grid; place-items: center; font-size: 12px; line-height: 1.3; font-weight: 700; color: var(--accent); box-shadow: inset 0 0 0 1.5px var(--line-hi); }
.ahs-tile.pop { animation: ahs-pop .22s var(--ease) both; }
@keyframes ahs-pop { from { opacity: 0; transform: scale(.82); } to { opacity: 1; transform: none; } }
.ahs-foot { padding: 0 10px 12px; }
.ah-fly { position: fixed; left: 0; top: 0; z-index: 6; transform-origin: 50% 50%; width: 40px; height: 40px; pointer-events: none; will-change: transform, opacity; transition: opacity .2s var(--ease); }
.ah-fly.out { opacity: 0; }
.ah-live { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
@media (prefers-reduced-motion: reduce) {
  .ah, .ah.on, .ahs, .ahs.on { animation: none; transition: opacity .12s linear; }
  .ah-row, .ah-row.fade, .ah-row.fade.shut, .ah-ask, .ah-ask.open { transition: opacity .12s linear, visibility 0s; }
  .ahs-tile.pop, .ah-x[aria-pressed="true"] { animation: none; }
}
@media (forced-colors: active) {
  .ah, .ahs { clip-path: none; padding: 0; border: 2px solid CanvasText; }
  .ah-in, .ahs-in { clip-path: none; }
  .ah-dot { background: CanvasText; box-shadow: none; }
  .ah-chip, .ahs-chip { clip-path: none; border: 1px solid ButtonText; }
}
`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const X_SVG = '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" focusable="false"><path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="2" stroke-linecap="square"/></svg>';
  const S = (n) => R.sfx?.play(n);
  const reduce = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const dpr = () => Math.max(1, Math.round((window.devicePixelRatio || 1) * 2) / 2);
  const now = () => performance.now() / 1000;

  // ---------- the DOM ----------
  const col = mk('div', 'ah-col');
  const home = mk('section', 'ah gone');
  home.setAttribute('role', 'region'); home.setAttribute('aria-label', 'Agents');
  const homeIn = mk('div', 'ah-in'), head = mk('div', 'ah-head'), dot = mk('span', 'ah-dot'), count = mk('span', 'ah-count');
  dot.setAttribute('aria-hidden', 'true');
  // The region is named "Agents"; the header itself is just the counts, on one line.
  head.append(dot, count);
  const list = mk('ol', 'ah-rows');
  homeIn.append(head, list); home.append(homeIn);
  const shelvesEl = mk('div', 'ah-shelves');
  Object.assign(shelvesEl.style, { display: 'flex', flexDirection: 'column', gap: '8px' });
  const live = mk('div', 'ah-live'); live.setAttribute('aria-live', 'polite');
  col.append(home, shelvesEl);
  const mount = () => { if (!col.isConnected) document.body.append(col, live); };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  const say = (text) => { live.textContent = ''; setTimeout(() => { live.textContent = text; }, 30); };

  const rows = new Map(); // id → row
  const shelves = []; // newest first
  const flights = new Set();
  let hatch = null;
  const send = (msg) => api.send?.(msg);
  const sizeCanvas = (c, css) => { const d = dpr(); c.width = css * d; c.height = css * d; c.style.width = c.style.height = `${css}px`; return d; };

  // ---------- rows ----------
  // A file name on one line of its tile, measured as drawn: a long one is shortened before its extension, which
  // always shows, the ellipsis standing in for the dot ("landing-pa…html"). Never broken onto a second line.
  // Again once the font has loaded.
  const measure = document.createElement('canvas').getContext('2d');
  const fitName = (span) => {
    const name = span.dataset.name, tile = span.parentElement, cs = getComputedStyle(tile);
    const room = tile.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 1;
    const fs = getComputedStyle(span);
    measure.font = `${fs.fontWeight} ${fs.fontSize} ${fs.fontFamily}`;
    const fits = (t) => measure.measureText(t).width <= room;
    if (room <= 0 || fits(name)) { span.textContent = name; return; }
    const dot = name.lastIndexOf('.'), ext = dot > 0 && name.length - dot <= 6 ? name.slice(dot + 1) : '';
    let head = ext ? name.slice(0, dot) : name;
    while (head.length > 1 && !fits(`${head}…${ext}`)) head = head.slice(0, -1);
    span.textContent = `${head}…${ext}`;
  };
  const fitNames = (root) => { for (const n of root.querySelectorAll('.ahs-name[data-name]')) fitName(n); };
  const refit = () => { for (const s of shelves) fitNames(s.el); };
  document.fonts?.load('400 11px "Martian Mono"').then(refit, () => {});
  document.fonts?.addEventListener?.('loadingdone', refit);
  const statusOf = (a) => (a.state === 'waiting' ? 'Waiting for your yes' : a.state === 'failed' ? `Couldn't finish: ${a.reason ?? 'something went wrong'}` : a.step || 'Starting');
  function makeRow(a) {
    const el = mk('li', 'ah-row shut'), inner = mk('div', 'ah-row-in'), line = mk('div', 'ah-line');
    const mini = mk('canvas', 'ah-mini'); mini.setAttribute('aria-hidden', 'true');
    const text = mk('div', 'ah-text'), title = mk('span', 'ah-title'), status = mk('span', 'ah-status');
    const x = mk('button', 'ah-x'); x.type = 'button';
    x.innerHTML = `<span class="no" aria-hidden="true">${X_SVG}</span><span class="yes" aria-hidden="true">Confirm</span>`;
    x.setAttribute('aria-pressed', 'false');
    const ask = mk('div', 'ah-ask'), q = mk('p', 'ah-q'), btns = mk('div', 'ah-btns');
    const yes = mk('button', 'ah-chip yes', 'Allow'), no = mk('button', 'ah-chip', "Don't allow");
    yes.type = no.type = 'button';
    const askIn = mk('div', 'ah-ask-in'), askPad = mk('div', 'ah-ask-pad');
    btns.append(yes, no); askPad.append(q, btns); askIn.append(askPad); ask.append(askIn);
    text.append(title, status); line.append(mini, text, x); inner.append(line, ask); el.append(inner);
    const row = { id: a.id, el, mini, ctx: mini.getContext('2d'), title, status, x, ask, q, yes, no, agent: a, pose: 'work', poseAt: now(), hidden: false, seed: Math.random() * 6 };
    row.d = sizeCanvas(mini, 32);
    x.addEventListener('click', () => {
      if (row.agent.state === 'failed') { send({ type: 'agent_dismiss', id: row.id }); removeRow(row, 'stop'); return; }
      if (armed !== row) { arm(row); return; }
      disarm();
      x.disabled = true; row.status.textContent = 'Stopping…';
      send({ type: 'agent_stop', id: row.id });
    });
    const answer = (allow) => {
      yes.disabled = no.disabled = true;
      send({ type: 'agent_answer', id: row.id, allow });
      // Shown at once; the next update confirms it.
      row.agent = { ...row.agent, state: 'working', ask: null, step: allow ? 'Carrying on' : 'Carrying on without that' };
      paint(row);
    };
    yes.addEventListener('click', () => answer(true));
    no.addEventListener('click', () => answer(false));
    rows.set(a.id, row);
    list.prepend(el);
    paint(row);
    return row;
  }
  function paint(row) {
    const a = row.agent;
    row.el.dataset.state = a.state;
    row.title.textContent = a.title;
    row.title.title = a.title;
    if (!row.x.disabled || a.state === 'failed') row.status.textContent = statusOf(a);
    row.status.title = row.status.textContent;
    const failed = a.state === 'failed';
    if (failed && armed === row) disarm();
    row.x.setAttribute('aria-label', armed === row ? `Confirm: stop ${a.title}` : `${failed ? 'Dismiss' : 'Stop'} ${a.title}`);
    if (failed) row.x.disabled = false;
    const waiting = a.state === 'waiting' && !!a.ask;
    if (waiting) { row.q.textContent = a.ask.question; row.yes.disabled = row.no.disabled = false; }
    row.ask.classList.toggle('open', waiting);
    const pose = failed ? 'fail' : waiting ? 'wait' : 'work';
    if (pose !== row.pose && row.pose !== 'done' && row.pose !== 'stop') { row.pose = pose; row.poseAt = now(); }
    countLine();
  }
  // Stop asks first: the first press arms it (Confirm), the second stops; 4 s or a click elsewhere puts it back.
  let armed = null;
  function arm(row) {
    disarm();
    armed = row;
    row.x.setAttribute('aria-pressed', 'true');
    row.x.setAttribute('aria-label', `Confirm: stop ${row.agent.title}`);
    row.armTimer = setTimeout(() => { if (armed === row) disarm(); }, 4000);
  }
  function disarm() {
    const row = armed;
    if (!row) return;
    armed = null; clearTimeout(row.armTimer);
    row.x.setAttribute('aria-pressed', 'false');
    row.x.setAttribute('aria-label', `${row.agent.state === 'failed' ? 'Dismiss' : 'Stop'} ${row.agent.title}`);
  }
  document.addEventListener('pointerdown', (e) => { if (armed && !e.target.closest?.('.ah-x[aria-pressed="true"]')) disarm(); }, true);
  function countLine() {
    const all = [...rows.values()].filter((r) => !r.leaving).map((r) => r.agent.state);
    const w = all.filter((s) => s === 'working').length, q = all.filter((s) => s === 'waiting').length, f = all.filter((s) => s === 'failed').length;
    const left = [...rows.values()].filter((r) => r.leaving), d = left.filter((r) => r.pose === 'done').length, x = left.length - d;
    count.textContent = [w && `${w} working`, q && `${q} waiting`, f && `${f} failed`].filter(Boolean).join(' · ')
      || [d && `${d} done`, x && `${x} stopped`].filter(Boolean).join(' · ');
  }
  function removeRow(row, pose) {
    if (row.leaving) return;
    row.leaving = true; row.pose = pose; row.poseAt = now();
    countLine();
    const gone = () => glide(() => { row.el.remove(); rows.delete(row.id); showHome(); });
    const fold = () => {
      const fadeOut = () => { row.el.classList.add('fade'); setTimeout(() => { row.el.classList.add('shut'); setTimeout(gone, reduce() ? 0 : 190); }, reduce() ? 130 : 270); };
      // The last row leaves with the home, which dissolves as a whole (unless a new agent came in meanwhile).
      if (anyLive()) return fadeOut();
      showHome();
      setTimeout(() => (anyLive() ? fadeOut() : gone()), reduce() ? 130 : 270);
    };
    setTimeout(fold, pose === 'done' ? (reduce() ? 300 : 1200) : reduce() ? 100 : 450);
    wake();
  }
  // When something above them comes or goes, the shelves glide to their new place instead of jumping (FLIP).
  let gliding = false;
  const nextShelves = [], flushShelves = () => { while (nextShelves.length) addShelf(nextShelves.shift()); };
  function glide(mutate) {
    if (gliding) return mutate();
    const els = [...shelvesEl.children], before = els.map((el) => el.getBoundingClientRect().top);
    for (const el of els) el.glide?.cancel();
    gliding = true;
    try { mutate(); } finally { gliding = false; }
    if (reduce()) return;
    els.forEach((el, i) => {
      const d = before[i] - el.getBoundingClientRect().top;
      if (el.isConnected && Math.abs(d) > 0.5) el.glide = el.animate([{ transform: `translateY(${d}px)` }, { transform: 'translateY(0)' }], { duration: 280, easing: 'cubic-bezier(.2,.8,.2,1)' });
    });
  }
  const anyLive = () => [...rows.values()].some((r) => !r.leaving);
  let goneT = 0;
  function showHome() {
    if (anyLive()) {
      clearTimeout(goneT);
      if (home.classList.contains('gone')) { glide(() => home.classList.remove('gone')); void home.offsetWidth; }
      home.classList.add('on');
    } else {
      const was = home.classList.contains('on');
      home.classList.remove('on');
      clearTimeout(goneT);
      const hide = () => { if (!rows.size && !home.classList.contains('gone')) glide(() => { home.classList.add('gone'); flushShelves(); }); };
      if (was) goneT = setTimeout(hide, reduce() ? 130 : 270); else hide();
    }
  }
  const openRow = (row) => { requestAnimationFrame(() => row.el.classList.remove('shut')); };

  // ---------- the made shelf ----------
  function addShelf(a) {
    for (const s of shelves.filter((s) => s.id === a.id)) closeShelf(s, false);
    const el = mk('section', 'ahs'), inner = mk('div', 'ahs-in');
    el.setAttribute('role', 'region'); el.setAttribute('aria-label', `What the agent did: ${a.title}`);
    const headEl = mk('div', 'ahs-head'), mini = mk('canvas', 'ahs-mini'), title = mk('span', 'ahs-title', a.title), x = mk('button', 'ahs-x');
    // Its title, then what it did in one line (the whole of it on hover), never a growing block.
    mini.setAttribute('aria-hidden', 'true'); title.title = a.title;
    x.type = 'button'; x.innerHTML = X_SVG; x.setAttribute('aria-label', `Close ${a.title}`);
    headEl.append(mini, title, x);
    inner.append(headEl);
    if (a.result?.outcome) { const o = mk('p', 'ahs-out', a.result.outcome); o.title = a.result.outcome; inner.append(o); }
    if (a.result?.needs_you) { const n = mk('p', 'ahs-need', `Needs you: ${a.result.needs_you}`); n.title = n.textContent; inner.append(n); }
    const files = a.result?.files ?? [], shown = files.length > 6 ? files.slice(0, 5) : files;
    const tiles = [];
    if (files.length) {
      const grid = mk('div', 'ahs-grid');
      for (const f of shown) {
        const b = mk('button', 'ahs-tile'); b.type = 'button';
        b.setAttribute('aria-label', `Open ${f.name}`); if (f.what) b.title = f.what;
        b.innerHTML = R.minis?.icon(R.minis.fileKind(f.name, f.dir)) ?? '';
        const n = mk('span', 'ahs-name', f.name); n.dataset.name = f.name;
        b.append(n);
        b.addEventListener('click', () => send({ type: 'agent_open', id: a.id, path: f.rel }));
        grid.append(b); tiles.push(b);
      }
      if (files.length > shown.length) {
        const b = mk('button', 'ahs-tile'); b.type = 'button';
        const more = files.length - shown.length;
        b.setAttribute('aria-label', `Open the folder with ${more} more`);
        b.append(mk('span', 'ahs-more', `+${more}`), mk('span', 'ahs-name', 'more'));
        b.addEventListener('click', () => send({ type: 'agent_folder', id: a.id, files: true }));
        grid.append(b); tiles.push(b);
      }
      inner.append(grid);
    }
    const label = R.agentOpenLabel(a.result);
    if (label) {
      const foot = mk('div', 'ahs-foot'), open = mk('button', 'ahs-chip', label);
      open.type = 'button';
      if (a.result?.place?.name) open.title = a.result.place.name;
      open.addEventListener('click', () => send({ type: 'agent_folder', id: a.id }));
      foot.append(open);
      if (!files.length) foot.style.paddingTop = '10px';
      inner.append(foot);
    } else inner.lastElementChild.style.paddingBottom = '12px';
    el.append(inner);
    const shelf = { id: a.id, el, mini, ctx: mini.getContext('2d'), at: now(), seed: Math.random() * 6 };
    shelf.d = sizeCanvas(mini, 24);
    x.addEventListener('click', () => { send({ type: 'agent_dismiss', id: a.id }); closeShelf(shelf, true); });
    shelves.unshift(shelf);
    glide(() => shelvesEl.prepend(el));
    while (shelves.length > 3) closeShelf(shelves[shelves.length - 1], false);
    setTimeout(place, 260);
    fitNames(el);
    requestAnimationFrame(() => {
      el.classList.add('on');
      tiles.forEach((t, i) => { t.style.animationDelay = `${120 + i * 40}ms`; t.classList.add('pop'); });
    });
    wake();
  }
  function closeShelf(s, sound) {
    const i = shelves.indexOf(s);
    if (i < 0) return;
    shelves.splice(i, 1);
    if (sound) S('poof');
    s.el.classList.remove('on');
    setTimeout(() => glide(() => s.el.remove()), reduce() ? 130 : 270);
  }

  // ---------- hatching and the launch flight ----------
  function flyCanvas(at) {
    const c = mk('canvas', 'ah-fly'); c.setAttribute('aria-hidden', 'true');
    const d = sizeCanvas(c, 40);
    document.body.append(c);
    const f = { c, ctx: c.getContext('2d'), d, x: at[0], y: at[1] };
    c.style.transform = `translate(${Math.round(at[0] - 20)}px, ${Math.round(at[1] - 20)}px)`;
    return f;
  }
  function startHatch(at) {
    if (hatch) return;
    hatch = { ...flyCanvas(at), at: now() };
    S('materialize');
    wake();
  }
  function endHatch() {
    if (!hatch) return;
    const h = hatch; hatch = null;
    h.c.classList.add('out');
    setTimeout(() => h.c.remove(), 220);
  }
  function launch(row, from) {
    if (reduce() || (!from && !hatch)) { endHatch(); openRow(row); return; }
    row.hidden = true;
    const f = hatch ?? flyCanvas(from);
    if (hatch === f) hatch = null;
    Object.assign(f, { row, t0: now(), from: [f.x, f.y], vx: 0, vy: 0 });
    flights.add(f);
    S('hop');
    openRow(row);
    wake();
  }
  // A 520 ms arc to the row's slot: squashed on take-off, stretched along the way, squashed on landing.
  function flyFrame(f, t) {
    const u = Math.min(1, (t - f.t0) / 0.52), r = f.row.mini.getBoundingClientRect(), to = [r.left + 16, r.top + 16];
    const e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2, dist = Math.hypot(to[0] - f.from[0], to[1] - f.from[1]);
    const x = f.from[0] + (to[0] - f.from[0]) * e, y = f.from[1] + (to[1] - f.from[1]) * e - Math.sin(Math.PI * u) * Math.min(120, dist * 0.35);
    if (f.px != null) { f.vx = (x - f.px) * 60; f.vy = (y - f.py) * 60; }
    f.px = x; f.py = y;
    f.c.style.transform = `translate(${Math.round(x - 20)}px, ${Math.round(y - 20)}px)`;
    const squash = u < 0.12 ? -Math.sin((u / 0.12) * Math.PI) * 0.6 : u > 0.88 ? -Math.sin(((u - 0.88) / 0.12) * Math.PI) * 0.6 : 0;
    R.minis?.draw(f.ctx, { size: 40, dpr: f.d, pose: 'fly', vx: f.vx, vy: f.vy, squash, time: t });
    if (u >= 1) {
      flights.delete(f); f.c.remove();
      f.row.hidden = false; f.row.pose = 'work'; f.row.poseAt = t;
      S('land');
    }
  }

  // ---------- the frame loop: only while something's on screen ----------
  let raf = 0;
  function wake() { if (!raf) raf = requestAnimationFrame(frame); }
  function frame() {
    raf = 0;
    const t = now(), still = reduce();
    if (hatch) R.minis?.draw(hatch.ctx, { size: 40, dpr: hatch.d, pose: 'hatch', t: t - hatch.at, time: t, reduce: still });
    for (const f of [...flights]) flyFrame(f, t);
    for (const row of rows.values()) {
      if (row.hidden) { row.ctx.clearRect(0, 0, row.mini.width, row.mini.height); continue; }
      R.minis?.draw(row.ctx, { size: 32, dpr: row.d, pose: row.pose, t: t - row.poseAt, time: t, glyph: row.agent.tool, reduce: still, seed: row.seed });
    }
    for (const s of shelves) R.minis?.draw(s.ctx, { size: 24, dpr: s.d, pose: 'done', t: t - s.at, time: t, reduce: true, seed: s.seed });
    // With reduced motion the poses hold still, so frames stop once nothing moves.
    const moving = hatch || flights.size || [...rows.values()].some((r) => r.leaving) || shelves.some((s) => t - s.at < 2);
    if (moving || (rows.size && !still)) raf = requestAnimationFrame(frame);
  }

  // ---------- events from the sidecar ----------
  const api = {
    send: null,
    hatch(at) { mount(); startHatch(at); },
    hatchOff: endHatch,
    event(e, { from = null } = {}) {
      mount();
      const a = e.agent;
      if (e.type === 'hatch_off') return endHatch();
      if (!a?.id) return;
      let row = rows.get(a.id);
      const fresh = !row;
      if (e.type === 'launch' || e.type === 'update' || e.type === 'ask') {
        if (row?.leaving) return;
        if (!row) row = makeRow(a);
        row.agent = a;
        if (row.x.disabled && a.state !== 'failed' && e.type === 'launch') row.x.disabled = false;
        paint(row);
        showHome();
        if (fresh) {
          if (e.type === 'launch') { launch(row, hatch ? [hatch.x, hatch.y] : from); say(`${a.title} started.`); }
          else openRow(row);
        }
        if (e.type === 'ask') { S('remind'); say(`${a.title} is waiting for your yes: ${a.ask?.question ?? ''}`); }
        if (a.state === 'failed' && fresh) row.pose = 'fail';
        wake();
        return;
      }
      if (e.type === 'done') {
        S('done');
        say(`${a.title} is done. ${a.result?.outcome ?? ''}`);
        if (row) { if (armed === row) disarm(); row.agent = a; row.x.style.visibility = 'hidden'; row.ask.classList.remove('open'); row.status.textContent = 'Done'; removeRow(row, 'done');
          // The last one out: its shelf takes the home's place as the home goes, so older shelves move just once.
          if (anyLive()) setTimeout(() => addShelf(a), reduce() ? 300 : 1100);
          else { nextShelves.push(a); setTimeout(() => glide(flushShelves), reduce() ? 700 : 1800); } }
        else addShelf(a);
        return;
      }
      if (e.type === 'fail') {
        S('error');
        say(`${a.title} couldn't finish: ${a.reason ?? ''}`);
        if (!row) { row = makeRow(a); openRow(row); }
        row.agent = a; row.pose = 'fail'; row.poseAt = now();
        paint(row); showHome(); wake();
        return;
      }
      if (e.type === 'gone') {
        S('poof');
        say(`${a.title} stopped.`);
        if (row) { if (armed === row) disarm(); row.status.textContent = 'Stopped'; row.ask.classList.remove('open'); removeRow(row, 'stop'); }
      }
    },
  };
  R.agentHome = api;

  // ---------- clicks on a click-through overlay ----------
  let rects = null, rectsAt = 0;
  const visibleRects = () => {
    if (rects && performance.now() - rectsAt < 100) return rects;
    rects = []; rectsAt = performance.now();
    if (!home.classList.contains('gone')) { const r = home.getBoundingClientRect(); if (r.width) rects.push([r.left, r.top, r.width, r.height]); }
    for (const s of shelves) { const r = s.el.getBoundingClientRect(); if (r.width) rects.push([r.left, r.top, r.width, r.height]); }
    return rects;
  };
  R.homeHit = (x, y) => visibleRects().some(([l, t, w, h]) => x >= l && x <= l + w && y >= t && y <= t + h);
  // Linux: part of the window's click shape (host.js).
  R.homeRects = () => visibleRects();
  // Top right of the screen this overlay is on, inside its safe area; again when it moves to another screen.
  const place = () => {
    const [x, y] = R.agentHomePlace([col.offsetWidth || 300, col.offsetHeight], R.safe, [innerWidth, innerHeight]);
    col.style.transform = `translate(${x}px, ${y}px)`;
    // A short screen: the oldest shelves go rather than running off its bottom.
    const room = innerHeight - (R.safe?.bottom ?? 0) - INSET - y;
    while (shelves.length > 1 && col.offsetHeight > room) { const s = shelves.pop(); s.el.remove(); }
  };
  addEventListener('resize', place);
  setInterval(place, 1000);
  place();
})();
