// Type to ask: a slim text field beside the cursor, opened by a tap of the typing shortcut, the Ask button, the tray
// or `roamies --ask`. It rides with the cursor like the listening pill does (voice/wave.swift), so it's always where
// you're looking and never in the way, and it's a real textarea: caret, selection, paste and IME just work.
// The research card's look (agent-panel.js): the stepped pixel frame, dark glass, Martian Mono.
// Click away with nothing typed and it closes; with something typed it waits where it is until you click it again.
// The overlay is click-through, so the host asks Roamie.askBarHit(x, y) whether the cursor is over it.
// API: Roamie.askBar.open({ placeholder, label, cursor, onKey, onPress, onLeft }) .ptr(x, y) .focus() .close() .value .isOpen
(() => {
  const R = window.Roamie;
  // Clear of the arrow: right of it and just below its tip, or left of it and above near the screen's edges.
  const AHEAD = 18, BELOW = 8, BEHIND = 8;
  // Where its top-left goes for a cursor [x, y], its size [w, h] and the room it may use { l, t, r, b }.
  R.askBarPlace = (cursor, size, bounds) => {
    const [cx, cy] = cursor, [w, h] = size;
    let x = cx + AHEAD, y = cy + BELOW;
    if (x + w > bounds.r) x = cx - BEHIND - w;
    if (y + h > bounds.b) y = cy - BELOW - h;
    return [Math.round(Math.max(bounds.l, Math.min(x, bounds.r - w))), Math.round(Math.max(bounds.t, Math.min(y, bounds.b - h)))];
  };
  if (typeof document === 'undefined') return;

  const LINE = 18, LINES = 5;
  const css = `
.ab { --ink: #e8fffa; --ink-2: #b4d8d1; --ink-3: #8ab8b0; --accent: #78ffe2; --line-hi: #2f7c74; --line-hot: #4fbfa9; --s: 2px;
  --notch: polygon(
    calc(var(--s) * 2) 0, calc(100% - var(--s) * 2) 0, calc(100% - var(--s) * 2) var(--s), calc(100% - var(--s)) var(--s),
    calc(100% - var(--s)) calc(var(--s) * 2), 100% calc(var(--s) * 2), 100% calc(100% - var(--s) * 2),
    calc(100% - var(--s)) calc(100% - var(--s) * 2), calc(100% - var(--s)) calc(100% - var(--s)),
    calc(100% - var(--s) * 2) calc(100% - var(--s)), calc(100% - var(--s) * 2) 100%, calc(var(--s) * 2) 100%,
    calc(var(--s) * 2) calc(100% - var(--s)), var(--s) calc(100% - var(--s)), var(--s) calc(100% - var(--s) * 2),
    0 calc(100% - var(--s) * 2), 0 calc(var(--s) * 2), var(--s) calc(var(--s) * 2), var(--s) var(--s), calc(var(--s) * 2) var(--s));
  position: fixed; left: 0; top: 0; z-index: 6; width: 320px; max-width: calc(100vw - 16px); box-sizing: border-box; padding: 2px;
  background: var(--line-hot); color: #051214; clip-path: var(--notch); cursor: text; will-change: transform;
  opacity: 0; visibility: hidden; pointer-events: none; transition: opacity .14s cubic-bezier(.2, .8, .2, 1), visibility 0s .14s; }
.ab.on { opacity: 1; visibility: visible; pointer-events: auto; transition: opacity .14s cubic-bezier(.2, .8, .2, 1); }
.ab-in { display: flex; align-items: flex-start; gap: 8px; padding: 8px 12px; background: rgba(5, 18, 20, .97); color: var(--ink); clip-path: var(--notch); }
.ab-mark { flex: none; width: 6px; height: 6px; margin-top: 6px; background: var(--accent); box-shadow: 0 0 6px var(--accent); }
.ab-in .ab-text { display: block; flex: 1; min-width: 0; height: 18px; box-sizing: border-box; margin: 0; padding: 0; border: 0; border-radius: 0;
  resize: none; overflow-x: hidden; overflow-y: hidden; overflow-wrap: anywhere; overscroll-behavior: contain;
  scrollbar-width: thin; scrollbar-color: var(--line-hi) transparent; appearance: none; -webkit-appearance: none;
  background-color: transparent; color: var(--ink); caret-color: var(--accent);
  font: 500 13.5px/18px "Martian Mono", ui-monospace, Menlo, monospace; cursor: text; }
/* Focus shows on the frame (lit while it has the keys, dull while it waits) and as the caret. The see-through
   outline is the ring forced-colours modes draw instead. */
.ab-in .ab-text:focus-visible { outline: 2px solid transparent; outline-offset: 0; }
.ab-in .ab-text::placeholder { color: var(--ink-3); opacity: 1; }
.ab-in .ab-text::selection { background: #1f5f58; color: #e8fffa; }
/* Waiting with something typed while you're in another app. */
.ab.away { background: var(--line-hi); color: #e8fffa; }
.ab.away .ab-in .ab-text { color: var(--ink-2); }
.ab.away .ab-mark { background: var(--line-hi); box-shadow: none; }
@media (prefers-reduced-motion: reduce) { .ab, .ab.on { transition: none; } }
@media (forced-colors: active) { .ab { clip-path: none; padding: 0; border: 2px solid CanvasText; } .ab-in { clip-path: none; } .ab-mark { background: CanvasText; box-shadow: none; } }
`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  let el = null, ta = null, on = false, away = false, at = [0, 0], size = [320, 38], cursor = null, openedAt = 0, cb = {};
  const bounds = () => { const S = R.safe ?? { top: 0, right: 0, bottom: 0, left: 0 }; return { l: S.left + 8, t: S.top + 8, r: innerWidth - S.right - 8, b: innerHeight - S.bottom - 8 }; };
  const put = (p) => { at = p; el.style.transform = `translate(${p[0]}px, ${p[1]}px)`; };
  const over = (x, y) => x >= at[0] && x <= at[0] + size[0] && y >= at[1] && y <= at[1] + size[1];
  // It holds still while the cursor is on it (so it can be clicked) and while it waits; else it's beside the cursor.
  const held = () => !cursor || away || over(cursor[0], cursor[1]);
  const follow = () => { if (!held()) put(R.askBarPlace(cursor, size, bounds())); };
  // One line, growing to five as you type (then it scrolls).
  const fit = () => {
    ta.style.height = `${LINE}px`;
    const full = ta.scrollHeight;
    ta.style.height = `${Math.max(LINE, Math.min(full, LINE * LINES))}px`;
    ta.style.overflowY = full > LINE * LINES ? 'auto' : 'hidden';
    size = [el.offsetWidth, el.offsetHeight];
    // Taller now: beside the cursor still, or (held still) kept on screen where it is.
    const B = bounds();
    if (held()) put([Math.max(B.l, Math.min(at[0], B.r - size[0])), Math.max(B.t, Math.min(at[1], B.b - size[1]))]);
    else follow();
  };
  const build = () => {
    el = document.createElement('div'); el.className = 'ab';
    const inner = document.createElement('div'); inner.className = 'ab-in';
    const mark = document.createElement('span'); mark.className = 'ab-mark'; mark.setAttribute('aria-hidden', 'true');
    ta = document.createElement('textarea'); ta.className = 'ab-text'; ta.rows = 1;
    ta.autocomplete = 'off'; ta.spellcheck = false; ta.setAttribute('autocapitalize', 'off'); ta.setAttribute('enterkeyhint', 'send');
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', (e) => { if (on) cb.onKey?.(e); });
    ta.addEventListener('focus', () => { away = false; el.classList.remove('away'); });
    ta.addEventListener('blur', () => {
      if (!on) return;
      // A stray blur while it opens (the window is still becoming the one that takes keys): ask for them again.
      if (performance.now() - openedAt < 300) { cb.onPress?.(); return; }
      if (!ta.value.trim()) { cb.onLeft?.(); return; }
      away = true; el.classList.add('away');
    });
    // A click anywhere on it takes the keys back (the overlay has to be made the key window first: the host does it).
    el.addEventListener('pointerdown', (e) => { if (e.target !== ta) e.preventDefault(); cb.onPress?.(); });
    inner.append(mark, ta);
    el.append(inner);
    document.body.append(el);
  };

  const api = {
    get isOpen() { return on; },
    get value() { return ta ? ta.value : ''; },
    open({ placeholder = '', label = '', cursor: c = null, onKey, onPress, onLeft } = {}) {
      if (!el) build();
      cb = { onKey, onPress, onLeft };
      ta.value = ''; ta.placeholder = placeholder;
      if (label) ta.setAttribute('aria-label', label);
      away = false; el.classList.remove('away');
      // No cursor seen yet (Wayland, until it crosses the overlay): the upper middle of the screen.
      const B = bounds();
      cursor = c ?? cursor ?? [(B.l + B.r) / 2 - 160 - AHEAD, B.t + (B.b - B.t) / 3];
      at = [-1e5, -1e5]; // nowhere yet, so the first placement is never "held still under the cursor"
      on = true; openedAt = performance.now();
      // Measured (which settles its starting, see-through style) and then shown at once, so it fades in and can
      // take the keys straight away: a hidden field can't be focused.
      fit();
      el.classList.add('on');
      return api;
    },
    // The cursor moved (every sample the host gets).
    ptr(x, y) { cursor = [x, y]; if (on) follow(); },
    focus() { if (on) ta.focus({ preventScroll: true }); },
    close() {
      if (!el || !on) return;
      on = false; away = false; cb = {};
      el.classList.remove('on', 'away');
      ta.blur(); // what was typed stays while it fades out; the next open clears it
    },
  };
  R.askBar = api;
  R.askBarHit = (x, y) => on && over(x, y);
  // Where it is, as [x, y, w, h] (null when closed): part of the window's click shape on Linux (host.js).
  R.askBarRect = () => (on ? [at[0], at[1], size[0], size[1]] : null);
})();
