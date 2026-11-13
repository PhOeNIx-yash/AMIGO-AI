// Diagnostics: one plain .log file a user can send (a GitHub issue, an email, a chat) when something goes wrong.
// Made only when they ask and saved on their computer; nothing is uploaded. Keys, tokens, emails, their home
// folder and user name are scrubbed before it's written.
import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const KEYS = [
  [/\b(?:sk|rk|pk)-(?:ant-|proj-|or-|svcacct-)?[A-Za-z0-9_-]{16,}/g, '<key>'],
  [/\bxai-[A-Za-z0-9_-]{16,}/g, '<key>'],
  [/\bAIza[0-9A-Za-z_-]{30,}/g, '<key>'],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}/g, '<key>'],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, '<token>'],
  [/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}/gi, '$1 <token>'],
  [/([?&](?:key|token|access_token|refresh_token|api_key|apikey|code|sig|signature)=)[^&\s"']+/gi, '$1<redacted>'],
  [/("(?:[A-Za-z_]*(?:key|token|secret|password)[A-Za-z_]*)"\s*:\s*)"[^"]{6,}"/gi, '$1"<redacted>"'],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '<email>'],
];
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Scrubs secrets and personal details from text: keys and tokens, emails, the home folder (as ~) and the user name. */
export function redact(text, { home = os.homedir(), user = safeUser() } = {}) {
  let s = String(text);
  if (home && home.length > 3) {
    // As written, with forward slashes, and JSON-escaped (Windows paths in logged JSON).
    for (const h of new Set([home, home.replace(/\\/g, '/'), home.replace(/\\/g, '\\\\')])) s = s.replace(new RegExp(escapeRe(h), 'gi'), '~');
  }
  for (const [re, to] of KEYS) s = s.replace(re, to);
  if (user && user.length > 2) s = s.replace(new RegExp(`(?<![A-Za-z0-9])${escapeRe(user)}(?![A-Za-z0-9])`, 'gi'), '<user>');
  return s;
}

function safeUser() {
  try { return os.userInfo().username; } catch { return ''; }
}

/** The settings, minus anything secret or personal (keys, tokens, names, emails). */
export function scrubConfig(value, key = '') {
  if (Array.isArray(value)) return value.map((v) => scrubConfig(v, key));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, scrubConfig(v, k)]));
  if (typeof value === 'string' && /key|token|secret|password|auth|cookie|name$|email/i.test(key) && !/^keys?$/i.test(key)) return value ? '<redacted>' : '';
  return value;
}

/** The last `max` bytes of a file, from the first whole line; '' when it's missing. */
export async function tail(file, max) {
  let fh;
  try {
    fh = await fs.open(file, 'r');
    const { size } = await fh.stat();
    const start = Math.max(0, size - max);
    const buf = Buffer.alloc(size - start);
    await fh.read(buf, 0, buf.length, start);
    let text = buf.toString('utf8');
    if (start > 0) text = `[… ${Math.round(start / 1024)} KB of older lines left out …]\n${text.slice(text.indexOf('\n') + 1)}`;
    return text;
  } catch {
    return '';
  } finally {
    await fh?.close().catch(() => {});
  }
}

const line = (title) => `\n${'-'.repeat(8)} ${title} ${'-'.repeat(Math.max(4, 72 - title.length))}\n`;
const show = (v) => (typeof v === 'string' ? v : JSON.stringify(v, null, 2));

/** The report as text: a header, then each section ([title, text or object]) in order, all of it redacted. */
export function buildReport({ now = new Date(), version = '0.0.0', sections = [] }, scrub = {}) {
  let out = `Roamies diagnostics\nMade ${now.toISOString()} by Roamies ${version}.\n`
    + 'Send this file to the Roamies developer: attach it to a GitHub issue (github.com/KenKaiii/Roamies/issues) or send it directly.\n'
    + 'Keys, tokens, emails, your home folder and user name have been removed. Look it over before sending if you like.\n';
  for (const [title, body] of sections) {
    const text = body == null || body === '' ? '(none)' : show(body);
    out += line(title) + text.replace(/\s+$/, '') + '\n';
  }
  return redact(out, scrub);
}

/** Runs a command and returns what it printed (or why it couldn't), never throwing. */
export function run(cmd, args, timeout = 8000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, maxBuffer: 4 << 20, windowsHide: true }, (err, stdout, stderr) => {
      const out = `${stdout ?? ''}${stderr ? `\n${stderr}` : ''}`.trim();
      resolve(err && !out ? `(couldn't run ${cmd}: ${err.code ?? err.message})` : out);
    });
  });
}

/** The newest activity logs (YYYY-MM-DD.log), oldest first, sharing `budget` bytes with the newest given most. */
export async function activityLogs(dir, budget) {
  const names = (await fs.readdir(dir).catch(() => [])).filter((f) => /^\d{4}-\d{2}-\d{2}\.log$/.test(f)).sort().reverse();
  const out = [];
  let left = budget;
  for (const f of names) {
    if (left < 4096) break;
    const text = await tail(path.join(dir, f), left);
    left -= Buffer.byteLength(text);
    out.unshift([`Activity log ${f}`, text]);
  }
  return out;
}

/** Where the report goes: Downloads, else the Desktop, else the home folder. */
export async function reportDir(home = os.homedir()) {
  for (const d of [path.join(home, 'Downloads'), path.join(home, 'Desktop')]) {
    if (await fs.stat(d).then((s) => s.isDirectory(), () => false)) return d;
  }
  return home;
}

export const reportName = (now = new Date()) => `roamies-diagnostics-${now.toISOString().slice(0, 19).replace(/[T:]/g, '-')}.log`;

/** Shows the saved file in Finder, Explorer or the file manager. */
export function reveal(file) {
  if (process.platform === 'darwin') execFile('open', ['-R', file], () => {});
  else if (process.platform === 'win32') execFile('explorer.exe', [`/select,"${file}"`], { windowsVerbatimArguments: true }, () => {});
  else execFile('xdg-open', [path.dirname(file)], () => {});
}
