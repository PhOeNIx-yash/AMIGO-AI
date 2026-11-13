// One place that words every error a person sees. explain() turns whatever went wrong into one short line that says
// what happened and what to do. The raw error never reaches the screen: it goes to the activity log and to Copy
// details. Causes Roamies doesn't recognise keep their area's code in the line, so a report can be traced.
import { redact } from './diagnostics.mjs';

/** The longest a line may be. */
export const MAX_LINE = 60;
/** The setup guide shows sign-in and key errors on one row of 430 px, about 7.8 px a character: this many fit, */
export const GUIDE_LINE = 55;
/** and this many with Copy details beside them on the same row. */
export const GUIDE_LINKED = 41;

const NET = /fetch failed|network|ENOTFOUND|EAI_AGAIN|ECONNRE(?:SET|FUSED)|ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|UND_ERR|socket hang up|timed? ?out|error sending request|dns error|connection (?:refused|reset|closed)/i;
const DISK_FULL = /ENOSPC|no space left|os error 28|disk full|not enough (?:disk )?space/i;
const NO_ACCESS = /EACCES|EPERM|EROFS|permission denied|operation not permitted|read-only|os error (?:13|30)\b/i;

// Each cause: [code, how to spot it (in the error's summary, or from the error itself), the line, offer Copy details].
// The first match wins. `where: 'model'` on a line means the fix is on the panel's Model page.
const AREAS = {
  'sign-in': {
    guide: true,
    code: 'E10',
    unknown: () => "Sign-in didn't work (E10). Try again.",
    known: [
      ['E11', /cancell?ed|AbortError/i, () => 'Sign-in cancelled.'],
      ['E12', (e, s) => e?.name === 'ChatGPTPlanNotEnabledError' || /plan use isn't enabled|access_denied/i.test(s), () => "Your ChatGPT plan can't be used here. Use an API key."],
      ['E13', /invalid_grant/i, (c) => `${c.who} sign-in didn't finish. Try again.`, true],
      ['E14', /invalid_client|different app registration|didn't finish registering/i, (c) => `${c.who} setup didn't finish. Try again.`, true],
      ['E15', /didn't come back from the browser|closed without a callback|No callback URL|Could not complete the sign-in|local sign-in listener/i, (c) => `${c.who} didn't come back from the browser. Try again.`],
      ['E16', /different ChatGPT account/i, () => 'That was a different ChatGPT account. Try again.'],
      ['E17', /ID token|signing keys|state mismatch|no authorization code|missing tokens/i, (c) => `${c.who}'s answer looked wrong. Try again.`, true],
      ['E18', /sign-in didn't finish/i, (c) => `${c.who} sign-in didn't finish. Try again.`],
      ['E19', DISK_FULL, () => 'Not enough disk space to sign in. Free some up.'],
      ['E1A', NET, (c) => `Can't reach ${c.who}. Check your internet.`],
    ],
  },
  reply: {
    code: 'E20',
    unknown: () => 'Something went wrong (E20). Ask me again.',
    known: [
      ['E21', /Claude Code isn't installed/i, () => 'Claude Code is missing. Sign in with Claude again.', false, 'model'],
      ['E22', /not logged in|NotLoggedIn|\b401\b|unauthori[sz]ed|invalid_grant|token refresh failed|authentication_error|(?:invalid|incorrect).{0,6}api.?key/i, (c) => `My ${c.who} login stopped working. Sign in again.`, false, 'model'],
      ['E23', /insufficient_quota|credit balance|billing|payment required|\b402\b/i, () => 'This account is out of credit. Top up or switch model.', false, 'model'],
      ['E24', /usage.?limit|quota|rate.?limit|\b429\b|too many requests/i, () => "I've hit this model's usage limit. Pick another model.", false, 'model'],
      ['E25', /context.{0,20}(?:length|window)|prompt is too long|too many tokens|maximum context/i, () => 'This chat got too long. Start a new one.'],
      ['E26', /model_not_found|model.{0,40}(?:not found|does not exist|not available|not supported)/i, () => "That model isn't available. Pick another one.", false, 'model'],
      ['E27', /overloaded|\b5(?:0[0234]|29)\b|service unavailable|bad gateway|internal server error/i, (c) => `${c.who} is having trouble right now. Try again soon.`],
      ['E28', NET, (c) => `Can't reach ${c.who}. Check your internet.`],
    ],
  },
  key: {
    guide: true,
    code: 'E30',
    unknown: () => "That key didn't save (E30). Try again.",
    known: [
      ['E31', /secret-tool isn't installed/i, () => 'Install secret-tool (libsecret), then try again.'],
      ['E32', /keyring|keychain|could not protect the key|secret service/i, (c) => (c.store ? `${c.store} didn't save the key. Try again.` : "The key didn't save safely. Try again."), true],
      ['E33', /\b40[13]\b|unauthori[sz]ed|(?:invalid|incorrect|bad).{0,6}(?:api.?)?key|refused/i, () => "That key didn't work. Copy it again and retry."],
      ['E34', DISK_FULL, () => 'Not enough disk space to save the key. Free some up.'],
      ['E35', NO_ACCESS, () => "The key didn't save to disk. Try again.", true],
      ['E36', NET, () => "Can't check the key. Check your internet."],
    ],
  },
  update: {
    code: 'E40',
    unknown: () => "The update didn't work (E40). Try again later.",
    known: [
      ['E41', /signature|minisign/i, () => "The update didn't pass its safety check. Try again later.", true],
      ['E42', DISK_FULL, () => 'Not enough disk space for the update. Free some up.'],
      ['E43', NO_ACCESS, () => "I couldn't replace the app. Reinstall it from GitHub.", true],
      ['E44', /valid release JSON|platform .{0,40}not found|\b404\b/i, () => "Couldn't check for updates. Check your internet."],
      ['E45', NET, () => "The update didn't download. Check your internet."],
    ],
  },
  hearing: {
    code: 'E50',
    unknown: () => "I couldn't hear that (E50). Try again.",
    known: [
      ['E51', /onnx|parakeet|voice model|model (?:file|load|missing|not found)/i, () => "My voice model didn't load. Restart Roamies.", true],
      ['E52', NET, () => 'Speech recognition needs the internet. Try again.'],
    ],
  },
  screen: {
    code: 'E60',
    unknown: () => "That screenshot didn't work (E60). Try again.",
    known: [
      ['E61', /screen recording|not authori[sz]ed|permission|denied/i, () => 'I need Screen Recording permission for that.'],
      ['E62', /timed? ?out|didn't answer/i, () => 'The screenshot took too long. Try again.'],
    ],
  },
  diagnostics: {
    code: 'E70',
    unknown: () => "The diagnostics file didn't save (E70). Try again.",
    known: [
      ['E71', DISK_FULL, () => 'Not enough disk space for the diagnostics file.'],
      ['E72', NO_ACCESS, () => "I couldn't write the diagnostics file there."],
    ],
  },
};
export const AREA_NAMES = Object.keys(AREAS);

// A name for a line (a provider, the key store): ours, but anything that isn't one short plain phrase is left out.
const name = (v, fallback) => (typeof v === 'string' && /^[A-Za-z][\w .()-]{0,23}$/.test(v) ? v : fallback);
const upper = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// A long name would wrap the row: say the line without one.
const limit = (a, copy) => (!a.guide ? MAX_LINE : copy ? GUIDE_LINKED : GUIDE_LINE);
const fitter = (ctx) => {
  const c = { who: name(ctx.who, 'the AI'), store: name(ctx.store, '') };
  return (line, max) => { const t = line(c); return upper(t.length <= max ? t : line({ who: 'the AI', store: '' })); };
};

const FIELDS = ['code', 'reason', 'status', 'detail', 'returning', 'retried', 'callbackToExchangeMs'];
// What went wrong, in full: each error in the chain as "Name: message [fields]", then the stack. Patterns read the
// summary only (a stack is file paths, which match words by accident).
function describe(err) {
  if (err == null || err === '') return { summary: 'unknown error', raw: 'unknown error' };
  if (typeof err !== 'object') { const s = String(err); return { summary: s.slice(0, 2000), raw: s.slice(0, 6000) }; }
  const parts = [];
  for (let e = err, n = 0; e != null && n < 4; e = e.cause, n++) {
    if (typeof e !== 'object') { parts.push(String(e)); break; }
    const fields = FIELDS.filter((k) => e[k] != null && e[k] !== '' && typeof e[k] !== 'object').map((k) => `${k}=${e[k]}`).join(' ');
    parts.push(`${e.name && e.name !== 'Error' ? `${e.name}: ` : ''}${e.message ?? ''}${fields ? ` [${fields}]` : ''}`);
  }
  const summary = (parts.join(' <- ') || 'unknown error').slice(0, 2000);
  const stack = typeof err.stack === 'string' ? err.stack.split('\n').filter((l) => /^\s+at /.test(l)).slice(0, 12).join('\n') : '';
  return { summary, raw: `${summary}${stack ? `\n${stack}` : ''}`.slice(0, 6000) };
}

/**
 * The line to show for an error. area: one of AREA_NAMES. ctx: { who: the provider's name, store: where keys are kept }.
 * Returns { code, text, known, copy, where, raw }: text is one line of at most MAX_LINE characters (fewer where the
 * setup guide shows it) built only from the table above; copy says whether to offer Copy details; raw is the full error, for the log and Copy details.
 */
export function explain(area, err, ctx = {}) {
  const a = AREAS[area] ?? AREAS.reply;
  const { summary, raw } = describe(err);
  const fit = fitter(ctx);
  for (const [code, spot, line, copy = false, where = null] of a.known) {
    if (typeof spot === 'function' ? spot(err, summary) : spot.test(summary)) return { code, text: fit(line, limit(a, copy)), known: true, copy, where, raw };
  }
  return { code: a.code, text: fit(a.unknown, limit(a, true)), known: false, copy: true, where: null, raw };
}

/** Every line there is, as worded for `ctx`: [{ area, code, text, copy, max }]. For checking them all at once. */
export function everyLine(ctx = {}) {
  const fit = fitter(ctx);
  return Object.entries(AREAS).flatMap(([area, a]) => [
    { area, code: a.code, text: fit(a.unknown, limit(a, true)), copy: true, max: limit(a, true) },
    ...a.known.map(([code, , line, copy = false]) => ({ area, code, text: fit(line, limit(a, copy)), copy, max: limit(a, copy) })),
  ]);
}

/** What Copy details puts on the clipboard: enough to trace the error, with keys, tokens and names scrubbed. */
export function detailsText(problem, { version = '0.0.0', os = '' } = {}, scrub = {}) {
  const at = new Date(problem?.at ?? Date.now()).toISOString();
  return redact([
    `Roamies error ${problem?.code ?? '?'} (${problem?.area ?? '?'})`,
    `Version: ${version}`,
    `OS: ${os}`,
    `Time: ${at}`,
    `Shown: ${problem?.text ?? ''}`,
    '',
    String(problem?.raw ?? 'unknown error').slice(0, 6000),
  ].join('\n'), scrub);
}
