// Spoken replies through VoiceStudio, the local voice app (voicestudio.sh), on this computer: its streaming
// text-to-speech socket (ws://127.0.0.1:3900/ws/tts), in whichever engine VoiceStudio has selected, in one of
// the user's saved VoiceStudio voices (a cloned or designed profile) or the engine's default.
// - One socket per reply, opened as soon as the reply starts. Each sentence is one request on it ({ text, voice }),
//   sent once the one before it is done; the answer is { type: 'start', sample_rate } JSON, raw 16-bit mono PCM
//   chunks, then { type: 'done' } (or { type: 'error', detail }).
// - VoiceStudio has no cancel request: stopping closes the socket, which ends the stream (and the rest of the reply).
// - It never falls back to anything else: a failure is reported (onError, with a reason) and the reply stays text.
// Loopback calls need no key. The address: ROAMIES_VOICESTUDIO_URL (loopback only), else 127.0.0.1:3900.

export const STUDIO_DEFAULT_URL = 'http://127.0.0.1:3900';
export const STUDIO_RATE = 24000;

/** VoiceStudio's base URL; anything that isn't a loopback http URL falls back to the default. */
export function studioUrl(env = process.env) {
  const raw = env.ROAMIES_VOICESTUDIO_URL;
  if (!raw) return STUDIO_DEFAULT_URL;
  try {
    const u = new URL(raw);
    if (u.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) return u.origin;
  } catch { /* not a URL */ }
  return STUDIO_DEFAULT_URL;
}

// What the user reads when it isn't working, by reason.
export const STUDIO_MESSAGES = {
  offline: 'Run VoiceStudio first.',
  starting: 'VoiceStudio is still starting up. Give it a moment, then press Check again.',
  other: "Something else is answering on VoiceStudio's port, not VoiceStudio.",
  engine: "VoiceStudio's voice engine isn't ready",
  voice: "That VoiceStudio voice isn't there anymore. Pick another one on the Voice page.",
  error: 'VoiceStudio ran into a problem',
  timeout: 'VoiceStudio took too long to answer.',
  closed: 'VoiceStudio closed the connection.',
};

/**
 * Is VoiceStudio up (and really VoiceStudio), is its selected engine usable, and which voices are saved there?
 * Never throws. { ok, reason, message, engine, engineName, voices: [{ id, name }] }
 */
export async function studioStatus({ fetchImpl = fetch, base = studioUrl(), timeout = 3000 } = {}) {
  let r;
  try { r = await fetchImpl(`${base}/health`, { signal: AbortSignal.timeout(timeout) }); }
  catch { return { ok: false, reason: 'offline', message: STUDIO_MESSAGES.offline }; }
  // Every VoiceStudio response says so in this header; anything else on the port isn't it.
  if (!r.headers.get('x-omnivoice-backend')) return { ok: false, reason: 'other', message: STUDIO_MESSAGES.other };
  const health = await r.json().catch(() => ({}));
  if (r.status === 503 || health.status === 'starting') return { ok: false, reason: 'starting', message: STUDIO_MESSAGES.starting };
  if (!r.ok) return { ok: false, reason: 'error', message: `${STUDIO_MESSAGES.error} (HTTP ${r.status}).` };

  let engines;
  try { engines = await (await fetchImpl(`${base}/engines/tts`, { signal: AbortSignal.timeout(timeout) })).json(); }
  catch { return { ok: false, reason: 'error', message: `${STUDIO_MESSAGES.error}: it didn't say which engine it uses.` }; }
  const row = (engines?.backends ?? []).find((b) => b.id === engines?.active);
  // Its built-in engine is called "VoiceStudio (k2-fsa/OmniVoice, …)": name the model then, not the app again.
  let engineName = row?.display_name ? String(row.display_name).replace(/\s*\(.*\)\s*$/, '') : engines?.active ?? null;
  if (engineName === 'VoiceStudio' && engines?.active_model) engineName = String(engines.active_model).split('/').pop();
  const base0 = { engine: engines?.active ?? null, engineName, version: health.version ?? null };
  if (!row || row.available === false) {
    return { ...base0, ok: false, reason: 'engine', message: `${STUDIO_MESSAGES.engine}${engineName ? ` (${engineName})` : ''}${row?.reason ? `: ${String(row.reason).slice(0, 160)}` : ''}. Pick or install an engine in VoiceStudio, then press Check again.` };
  }
  let voices = [];
  try {
    const j = await (await fetchImpl(`${base}/v1/audio/voices`, { signal: AbortSignal.timeout(timeout) })).json();
    // Saved voices (cloned or designed) only; the OpenAI-style aliases all mean "the engine's default".
    voices = (Array.isArray(j?.voices) ? j.voices : []).filter((v) => v?.type === 'profile' && typeof (v.voice_id ?? v.id) === 'string')
      .map((v) => ({ id: v.voice_id ?? v.id, name: typeof v.name === 'string' && v.name ? v.name : v.voice_id ?? v.id }));
  } catch { /* usable without the list: the engine's default voice */ }
  return { ...base0, ok: true, reason: null, message: null, voices };
}

// Voice cues ([warm], [laughing]) are Fish Audio's; VoiceStudio's engines would read them out.
export const plain = (text) => String(text ?? '').replace(/\[[^\]]*\]/g, ' ').replace(/\s+/g, ' ').trim();

const fail = (reason, detail, heard) => {
  const msg = STUDIO_MESSAGES[reason] ?? STUDIO_MESSAGES.error;
  const withDetail = detail && (reason === 'error' || reason === 'engine');
  const e = new Error(withDetail ? `${msg}: ${String(detail).slice(0, 200)}` : /[.!?]$/.test(msg) ? msg : `${msg}.`);
  e.reason = reason; e.heard = heard;
  return e;
};

/**
 * One reply's voice, the same shape as FishVoice: say() as many times as needed, then end(); abort() stops at once.
 * onAudio(Uint8Array, rate) gets whole 16-bit samples; onEnd() after the last audio; onError(err) instead, with
 * err.reason ('offline' | 'starting' | 'voice' | 'timeout' | 'closed' | 'error') and err.heard.
 */
export class StudioVoice {
  constructor({ voice = null, base = studioUrl(), WebSocketImpl = globalThis.WebSocket, onAudio, onEnd, onError, openTimeout = 5000, sentenceTimeout = 90_000 }) {
    Object.assign(this, { voice, base, WebSocketImpl, onAudio, onEnd, onError, openTimeout, sentenceTimeout });
    this.queue = [];
    this.state = 'new'; // new → opening → open → (ending) → done
    this.ending = false;
    this.busy = false; // a sentence was sent and its "done" hasn't come
    this.heard = false;
    this.rate = STUDIO_RATE;
    this.odd = null;
    this.timer = null;
  }

  open() {
    if (this.state !== 'new') return this;
    this.state = 'opening';
    let ws;
    try { ws = new this.WebSocketImpl(`${this.base.replace(/^http/, 'ws')}/ws/tts`); }
    catch { this.fail('offline'); return this; }
    this.ws = ws;
    ws.binaryType = 'arraybuffer';
    this.arm(this.openTimeout, 'offline');
    ws.onopen = () => { if (this.state === 'done') return; this.state = 'open'; this.clear(); this.pump(); };
    ws.onmessage = (e) => {
      if (this.state === 'done') return;
      if (typeof e.data === 'string') {
        let m;
        try { m = JSON.parse(e.data); } catch { return; }
        if (m.type === 'start') { this.rate = Number(m.sample_rate) || STUDIO_RATE; this.odd = null; }
        else if (m.type === 'done') { this.busy = false; this.clear(); this.pump(); }
        else if (m.type === 'error') this.fail(/voice|profile/i.test(String(m.detail ?? '')) && /not found|unknown|missing/i.test(String(m.detail ?? '')) ? 'voice' : 'error', m.detail);
        return;
      }
      const pcm = this.whole(e.data);
      if (pcm.length) { this.heard = true; this.arm(this.sentenceTimeout, 'timeout'); this.onAudio?.(pcm, this.rate); }
    };
    ws.onerror = () => this.fail(this.state === 'opening' ? 'offline' : 'closed');
    // 1008 while starting up: VoiceStudio refuses sockets until it's ready.
    ws.onclose = (e) => { if (this.state !== 'done') this.fail(e?.code === 1008 ? 'starting' : this.state === 'opening' ? 'offline' : 'closed'); };
    return this;
  }

  say(text) {
    if (this.state === 'done' || this.ending) return;
    const t = plain(text);
    if (!t) return;
    if (this.state === 'new') this.open();
    this.queue.push(t);
    this.pump();
  }

  end() {
    if (this.state === 'done' || this.ending) return;
    this.ending = true;
    if (this.state === 'new') { this.state = 'done'; this.onEnd?.(); return; } // nothing was said
    this.pump();
  }

  abort() { this.close(); }

  pump() {
    if (this.state !== 'open' || this.busy) return;
    const text = this.queue.shift();
    if (text == null) { if (this.ending) this.finish(); return; }
    this.busy = true;
    this.ws.send(JSON.stringify({ text, ...(this.voice ? { voice: this.voice } : {}) }));
    this.arm(this.sentenceTimeout, 'timeout');
  }

  // PCM samples are 2 bytes; a chunk can end mid-sample, so carry the odd byte into the next one.
  whole(bytes) {
    let b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    if (this.odd != null) { const c = new Uint8Array(b.length + 1); c[0] = this.odd; c.set(b, 1); b = c; this.odd = null; }
    if (b.length % 2) { this.odd = b[b.length - 1]; b = b.subarray(0, b.length - 1); }
    return b;
  }

  arm(ms, reason) { this.clear(); this.timer = setTimeout(() => this.fail(reason), ms); }
  clear() { clearTimeout(this.timer); this.timer = null; }

  close() {
    if (this.state === 'done') return;
    this.state = 'done';
    this.clear();
    this.queue.length = 0;
    try { this.ws?.close(); } catch { /* already closed */ }
  }

  finish() { if (this.state === 'done') return; this.close(); this.onEnd?.(); }

  fail(reason, detail) {
    if (this.state === 'done') return;
    const heard = this.heard;
    this.close();
    this.onError?.(fail(reason, detail, heard));
  }
}
