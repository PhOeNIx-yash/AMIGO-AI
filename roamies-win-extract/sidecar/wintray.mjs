// Windows 11 starts a new tray icon hidden behind the ^ arrow, so people couldn't find Roamies (or its settings).
// There's no API to show it; Windows keeps the choice per program under HKCU\Control Panel\NotifyIconSettings\<id>
// (ExecutablePath, and IsPromoted: 1 shows it next to the clock). Explorer makes that entry once the icon has been
// added. Undocumented, so it's tried once and quietly given up on if the entry never shows up.
const ROOT = 'HKCU\\Control Panel\\NotifyIconSettings';

/** `reg query <ROOT> /s /v ExecutablePath` output → [{ key, path }]. */
export function trayEntries(out) {
  const entries = [];
  let key = null;
  for (const line of String(out ?? '').split(/\r?\n/)) {
    if (/^HKEY_/i.test(line)) { key = line.trim(); continue; }
    const m = /^\s+ExecutablePath\s+REG_(?:EXPAND_)?SZ\s+(.+?)\s*$/i.exec(line);
    if (m && key) entries.push({ key, path: m[1] });
  }
  return entries;
}

// The path may start with a known-folder GUID instead of the folder ("{F1B32785-…}\Roamies\roamies.exe"), so the
// program is matched on its folder and file name.
const tail = (p) => String(p).toLowerCase().split(/[\\/]+/).filter(Boolean).slice(-2).join('\\');
export const entriesFor = (entries, exe) => (exe ? entries.filter((e) => tail(e.path) === tail(exe) && tail(exe).includes('\\')) : []);

/** Shows this program's tray icon next to the clock. 'shown', 'missing' (no entry yet: try again later) or 'failed'.
 *  exec(cmd, args): { code, stdout } (secrets.mjs's runInput). */
export async function showTrayIcon({ exe, exec }) {
  const q = await exec('reg', ['query', ROOT, '/s', '/v', 'ExecutablePath']);
  if (q.code !== 0 && !q.stdout) return 'missing';
  const mine = entriesFor(trayEntries(q.stdout), exe);
  if (!mine.length) return 'missing';
  for (const e of mine) {
    const r = await exec('reg', ['add', e.key, '/v', 'IsPromoted', '/t', 'REG_DWORD', '/d', '1', '/f']);
    if (r.code !== 0) return 'failed';
  }
  return 'shown';
}
