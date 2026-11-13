// What's new: the panel page listing what changed (showcase/panel/whatsnew.json), opened by itself after an update.

/** a is a later version than b ("0.1.10" > "0.1.9"); unparseable parts count as 0. */
export function newer(a, b) {
  const pa = String(a ?? '').split('.').map((n) => parseInt(n, 10) || 0), pb = String(b ?? '').split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) > (pb[i] ?? 0);
  return false;
}

/** At launch: was this version just installed over an older one? → { open, from, seen }. A fresh install (not set up
 *  yet) or the same version never opens it; someone set up before this existed (no seen version) counts as updated. */
export function whatsNewOnStart({ seen, version, onboarded }) {
  if (!version || version === '0.0.0' || seen === version) return { open: false, from: undefined, seen };
  const updated = !!onboarded && (seen == null || newer(version, seen));
  return { open: updated, from: updated ? (seen ?? null) : undefined, seen: version };
}

export const KINDS = ['new', 'improved', 'fixed', 'removed'];
export const LIMITS = { updates: 50, title: 32, text: 88 };
/** The data file's rules (what the commit command writes): problems as plain lines, none when it's fine. */
export function checkWhatsNew(data) {
  const bad = [], ups = data?.updates;
  if (!Array.isArray(ups) || !ups.length) return ['updates must be a non-empty list'];
  if (ups.length > LIMITS.updates) bad.push(`${ups.length} updates; keep the newest ${LIMITS.updates}`);
  let last = '9999-99-99', released = false;
  ups.forEach((u, i) => {
    const at = `updates[${i}]`;
    if (u.version !== null && !/^\d+\.\d+\.\d+$/.test(String(u.version))) bad.push(`${at}.version must be "x.y.z" or null (not released yet)`);
    if (u.version === null && released) bad.push(`${at}: unreleased updates go above every released one`);
    if (u.version !== null) released = true;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(u.date))) bad.push(`${at}.date must be YYYY-MM-DD`);
    else if (u.date > last) bad.push(`${at}.date is newer than the one above it: newest first`);
    else last = u.date;
    if (!Array.isArray(u.items) || !u.items.length) bad.push(`${at}.items must list at least one change`);
    (u.items ?? []).forEach((it, j) => {
      const w = `${at}.items[${j}]`;
      if (!KINDS.includes(it.kind)) bad.push(`${w}.kind must be one of ${KINDS.join(', ')}`);
      for (const [k, max] of [['title', LIMITS.title], ['text', LIMITS.text]]) {
        const s = String(it[k] ?? '');
        if (!s.trim()) bad.push(`${w}.${k} is empty`);
        if (s.length > max) bad.push(`${w}.${k} is ${s.length} characters; keep it to ${max}`);
        if (/[—–]/.test(s)) bad.push(`${w}.${k} has a dash (— or –); use a comma or a full stop`);
      }
    });
  });
  return bad;
}
