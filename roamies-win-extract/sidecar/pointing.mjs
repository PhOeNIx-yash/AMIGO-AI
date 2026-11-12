// Screenshots reach the model resized (Retina pixels, then capped), so its x/y are image pixels, not screen points.
export function shotMap([x, y, w, h], imgW, imgH) {
  return { origin: [x, y], sx: w / imgW, sy: h / imgH, size: [imgW, imgH] };
}

/** Whether a picture of imgW×imgH can show the area [x, y, w, h]: resizing keeps the shape, so a different shape
 *  means the picture shows something else (CapCut's button had the bounds; the picture was its whole window). */
export function shotFits([, , w, h], imgW, imgH) {
  return w > 0 && h > 0 && imgW > 0 && imgH > 0 && Math.abs((w / h) / (imgW / imgH) - 1) <= 0.02;
}

/** The real control a loop drawn from screenshot pixels was meant for: the one it overlaps most, if they overlap well
 *  and are about the same size (so a loop round a group of icons stays round the group). Rects in screen points. */
export function snapRect(rect, controls) {
  const [x, y, w, h] = rect, area = w * h;
  if (!(area > 0)) return null;
  let best = null, bestFit = 0.35;
  for (const b of controls ?? []) {
    if (!Array.isArray(b) || !(b[2] > 0 && b[3] > 0)) continue;
    const ratio = (b[2] * b[3]) / area;
    if (ratio < 0.4 || ratio > 2.5) continue;
    const ix = Math.max(0, Math.min(x + w, b[0] + b[2]) - Math.max(x, b[0])), iy = Math.max(0, Math.min(y + h, b[1] + b[3]) - Math.max(y, b[1]));
    const fit = (ix * iy) / (area + b[2] * b[3] - ix * iy);
    if (fit > bestFit) { bestFit = fit; best = b; }
  }
  return best ? [...best] : null;
}

export function toScreen(map, x, y) {
  return [Math.round(map.origin[0] + x * map.sx), Math.round(map.origin[1] + y * map.sy)];
}

export function parseSipsSize(out) {
  const w = +/pixelWidth:\s*(\d+)/.exec(out)?.[1], h = +/pixelHeight:\s*(\d+)/.exec(out)?.[1];
  return w > 0 && h > 0 ? [w, h] : null;
}

export const sameBounds = (a, b) => !!a && !!b && a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= 1);

const same = (a, b) => a.role === b.role && (a.label ?? '').trim() === (b.label ?? '').trim();

// read_window ids are list positions, so after the page changes the same id can be another control.
// expected: what read_window listed at that id; fresh: the window's controls now. Returns the live control or null.
export function rematch(expected, fresh, id) {
  if (!expected) return null;
  const now = fresh[id - 1];
  if (now && same(expected, now)) return now;
  if (!(expected.label ?? '').trim()) return null; // nothing to recognise it by
  const [ex, ey] = expected.bounds ?? [0, 0];
  const hits = fresh.filter((c) => same(expected, c));
  hits.sort((a, b) => Math.hypot(a.bounds[0] - ex, a.bounds[1] - ey) - Math.hypot(b.bounds[0] - ex, b.bounds[1] - ey));
  return hits[0] ?? null;
}

const OLD_LOOK = "That's from before the user's latest message, and the screen may have changed since (another page, tab or app). Look again first.";

/** Why a screenshot can't be used to point any more, or null if it still can. */
export function shotProblem(shot, turn) {
  if (!shot) return 'Take a screenshot first, then give x, y in its pixels.';
  if (shot.unmapped) return "That screenshot doesn't line up with the window on screen, so its pixels can't be used to point or click. Point with the thing's visible words (text) instead.";
  if (shot.turn !== turn) return `The screenshot: ${OLD_LOOK}`;
  if (shot.used) return 'Something was clicked or opened since that screenshot, so it may be out of date. Take a new screenshot.';
  return null;
}

/** Why a read_window list can't be used for this window any more, or null if it still can. */
export function readProblem(read, windowId, turn) {
  if (!read || read.win !== windowId) return 'Call read_window on this window first, then use its ids.';
  if (read.turn !== turn) return `The read_window list: ${OLD_LOOK}`;
  return null;
}
